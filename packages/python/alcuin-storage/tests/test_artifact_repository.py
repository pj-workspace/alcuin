from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import psycopg
import pytest

from alcuin_storage import (
    ArtifactVersionConflict,
    PostgresStore,
    RepositoryConflict,
)


DATABASE_URL = os.environ.get("ALCUIN_TEST_POSTGRES_URL")


def require_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    if not urlsplit(url).path.removeprefix("/").endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_test_database(DATABASE_URL)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="ALCUIN_TEST_POSTGRES_URL is required for PostgreSQL Artifact tests",
)


def reset_database() -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """TRUNCATE TABLE
            artifact_versions, artifacts,
            message_attachments, attachment_blobs, attachments,
            run_context_assemblies, thread_compactions, messages,
            knowledge_documents, knowledge_sources, extensions, approvals, events,
            runs, threads, agent_versions, agents, workspaces
            CASCADE"""
        )


@pytest.fixture
def store() -> PostgresStore:
    assert DATABASE_URL is not None
    reset_database()
    repository = PostgresStore(DATABASE_URL, pool_max_size=4)
    try:
        yield repository
    finally:
        repository.close()


def create_running_run(store: PostgresStore, title: str = "Artifact thread") -> tuple[dict, dict]:
    agent = store.list_agents("ws_demo")[0]
    thread = store.create_thread("ws_demo", agent["id"], title, {})
    run = store.create_run(
        "ws_demo",
        thread["id"],
        agent["current_version_id"],
        "Generate an Artifact",
    )
    store.set_run_status("ws_demo", run["id"], "running")
    return thread, run


def test_artifact_event_is_atomic_idempotent_versioned_and_workspace_scoped(
    store: PostgresStore,
) -> None:
    thread, run = create_running_run(store)
    first_event = store.append_artifact_event(
        "ws_demo",
        run["id"],
        {
            "id": "provider-controlled-id-is-ignored",
            "title": "Risk brief",
            "kind": "document",
            "content": "# Risk brief\n\nInitial finding.",
        },
    )
    artifact = first_event["payload"]["artifact"]

    assert artifact["id"].startswith("art_")
    assert artifact["id"] != "provider-controlled-id-is-ignored"
    assert artifact["workspace_id"] == "ws_demo"
    assert artifact["thread_id"] == thread["id"]
    assert artifact["source_run_id"] == run["id"]
    assert artifact["content_type"] == "text/markdown"
    assert artifact["version"] == 1
    assert store.get_artifact("ws_other", artifact["id"]) is None
    assert store.list_thread_artifacts("ws_other", thread["id"]) == []
    assert store.get_artifact("ws_demo", artifact["id"]) == artifact
    assert store.list_thread_artifacts("ws_demo", thread["id"])[0] == artifact

    replay = store.append_artifact_event(
        "ws_demo",
        run["id"],
        {
            "title": "Risk brief",
            "kind": "document",
            "content": "# Risk brief\n\nInitial finding.",
        },
    )
    assert replay["id"] == first_event["id"]
    assert replay["sequence"] == first_event["sequence"]
    assert len(store.list_events("ws_demo", run["id"])) == 1

    second_event = store.append_artifact_event(
        "ws_demo",
        run["id"],
        {
            "title": "Risk brief v2",
            "kind": "report",
            "content_type": "text/plain",
            "content": "Updated runtime finding.",
        },
    )
    second = second_event["payload"]["artifact"]
    assert second["id"] == artifact["id"]
    assert second["version"] == 2
    assert second["kind"] == "report"
    assert second["content_type"] == "text/plain"
    assert second_event["sequence"] == first_event["sequence"] + 1

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        revisions = connection.execute(
            """SELECT version, title, kind, content_type, source
            FROM artifact_versions
            WHERE workspace_id = %s AND artifact_id = %s ORDER BY version""",
            ("ws_demo", artifact["id"]),
        ).fetchall()
    assert revisions == [
        (1, "Risk brief", "document", "text/markdown", "runtime"),
        (2, "Risk brief v2", "report", "text/plain", "runtime"),
    ]


def test_user_edit_requires_terminal_run_and_uses_optimistic_version(
    store: PostgresStore,
) -> None:
    _thread, run = create_running_run(store)
    generated = store.append_artifact_event(
        "ws_demo",
        run["id"],
        {
            "title": "Editable report",
            "kind": "document",
            "content": "Generated draft",
        },
    )["payload"]["artifact"]

    with pytest.raises(RepositoryConflict, match="source Run is terminal"):
        store.update_artifact(
            "ws_demo",
            generated["id"],
            expected_version=1,
            content="Too early",
        )

    store.set_run_status("ws_demo", run["id"], "completed")
    with pytest.raises(RepositoryConflict, match="terminal event"):
        store.update_artifact(
            "ws_demo",
            generated["id"],
            expected_version=1,
            content="Status alone is not terminal truth",
        )
    terminal_event = store.finalize_run_with_event(
        "ws_demo",
        run["id"],
        "completed",
        "Final visible response",
        4,
        "run.completed",
        {"status": "completed"},
    )
    edited = store.update_artifact(
        "ws_demo",
        generated["id"],
        expected_version=1,
        title="Edited report",
        content="User revision",
    )
    assert edited is not None
    assert edited["version"] == 2
    assert edited["title"] == "Edited report"
    assert edited["content"] == "User revision"

    unchanged = store.update_artifact(
        "ws_demo",
        generated["id"],
        expected_version=2,
        title="Edited report",
        content="User revision",
    )
    assert unchanged == edited

    with pytest.raises(ArtifactVersionConflict) as stale:
        store.update_artifact(
            "ws_demo",
            generated["id"],
            expected_version=1,
            content="Overwrite a newer version",
        )
    assert stale.value.current_version == 2
    assert store.get_artifact("ws_demo", generated["id"]) == edited

    with pytest.raises(RepositoryConflict, match="terminal Run"):
        store.append_artifact_event(
            "ws_demo",
            run["id"],
            {
                "title": "Late runtime overwrite",
                "kind": "document",
                "content": "Must be rejected",
            },
        )
    events = store.list_events("ws_demo", run["id"])
    assert events[-1]["id"] == terminal_event["id"]
    assert events[-1]["type"] == "run.completed"

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        sources = connection.execute(
            """SELECT version, source FROM artifact_versions
            WHERE workspace_id = %s AND artifact_id = %s ORDER BY version""",
            ("ws_demo", generated["id"]),
        ).fetchall()
    assert sources == [(1, "runtime"), (2, "user")]


def test_invalid_json_artifact_rolls_back_without_event_or_resource(
    store: PostgresStore,
) -> None:
    thread, run = create_running_run(store, "Invalid JSON")
    with pytest.raises(ValueError, match="application/json content is invalid"):
        store.append_artifact_event(
            "ws_demo",
            run["id"],
            {
                "title": "Broken JSON",
                "kind": "data",
                "content_type": "application/json",
                "content": "{not-json}",
            },
        )
    assert store.list_thread_artifacts("ws_demo", thread["id"]) == []
    assert store.list_events("ws_demo", run["id"]) == []


def test_artifact_and_revision_roll_back_when_canonical_event_insert_fails(
    store: PostgresStore,
) -> None:
    thread, run = create_running_run(store, "Atomic rollback")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """CREATE OR REPLACE FUNCTION reject_test_artifact_event()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.type = 'artifact.updated' THEN
                    RAISE EXCEPTION 'reject artifact event for atomicity test';
                END IF;
                RETURN NEW;
            END;
            $$"""
        )
        connection.execute(
            """CREATE TRIGGER reject_test_artifact_event_trigger
            BEFORE INSERT ON events FOR EACH ROW
            EXECUTE FUNCTION reject_test_artifact_event()"""
        )
    try:
        with pytest.raises(psycopg.Error, match="reject artifact event for atomicity test"):
            store.append_artifact_event(
                "ws_demo",
                run["id"],
                {
                    "title": "Must roll back",
                    "kind": "document",
                    "content": "No partial resource may remain.",
                },
            )
    finally:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "DROP TRIGGER IF EXISTS reject_test_artifact_event_trigger ON events"
            )
            connection.execute("DROP FUNCTION IF EXISTS reject_test_artifact_event()")

    assert store.list_thread_artifacts("ws_demo", thread["id"]) == []
    assert store.list_events("ws_demo", run["id"]) == []
    with psycopg.connect(DATABASE_URL) as connection:
        artifact_versions = connection.execute(
            "SELECT count(*) FROM artifact_versions"
        ).fetchone()
        sequence = connection.execute(
            "SELECT next_event_sequence FROM runs WHERE id = %s",
            (run["id"],),
        ).fetchone()
    assert artifact_versions == (0,)
    assert sequence == (0,)


def test_concurrent_user_edits_allow_exactly_one_expected_version_winner(
    store: PostgresStore,
) -> None:
    _thread, run = create_running_run(store, "Concurrent edits")
    artifact = store.append_artifact_event(
        "ws_demo",
        run["id"],
        {
            "title": "Concurrent report",
            "kind": "document",
            "content": "Original",
        },
    )["payload"]["artifact"]
    store.finalize_run_with_event(
        "ws_demo",
        run["id"],
        "completed",
        "Original",
        1,
        "run.completed",
        {"status": "completed"},
    )

    assert DATABASE_URL is not None
    barrier = threading.Barrier(2)

    def edit(content: str) -> tuple[str, int]:
        repository = PostgresStore(DATABASE_URL, pool_max_size=2)
        try:
            barrier.wait(timeout=5)
            try:
                updated = repository.update_artifact(
                    "ws_demo",
                    artifact["id"],
                    expected_version=1,
                    content=content,
                )
                assert updated is not None
                return "updated", int(updated["version"])
            except ArtifactVersionConflict as exc:
                return "conflict", exc.current_version
        finally:
            repository.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(edit, "First concurrent edit"),
            executor.submit(edit, "Second concurrent edit"),
        ]
        results = sorted(future.result() for future in futures)
    assert results == [("conflict", 2), ("updated", 2)]


def test_concurrent_artifact_and_regular_events_share_one_run_sequence(
    store: PostgresStore,
) -> None:
    _thread, run = create_running_run(store, "Concurrent events")
    assert DATABASE_URL is not None
    barrier = threading.Barrier(2)

    def append_artifact() -> dict:
        repository = PostgresStore(DATABASE_URL, pool_max_size=2)
        try:
            barrier.wait(timeout=5)
            return repository.append_artifact_event(
                "ws_demo",
                run["id"],
                {
                    "title": "Concurrent Artifact",
                    "kind": "document",
                    "content": "Persisted atomically",
                },
            )
        finally:
            repository.close()

    def append_message() -> dict:
        repository = PostgresStore(DATABASE_URL, pool_max_size=2)
        try:
            barrier.wait(timeout=5)
            return repository.append_event(
                "ws_demo",
                run["id"],
                "message.delta",
                {"delta": "Concurrent visible text"},
            )
        finally:
            repository.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(append_artifact), executor.submit(append_message)]
        written = [future.result() for future in futures]
    assert sorted(event["sequence"] for event in written) == [1, 2]
    persisted = store.list_events("ws_demo", run["id"])
    assert [event["sequence"] for event in persisted] == [1, 2]
    assert {event["type"] for event in persisted} == {
        "artifact.updated",
        "message.delta",
    }
