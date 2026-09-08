from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
)
from alcuin_storage import ControlPlaneRepository, RepositoryConflict
from alcuin_storage.postgres import PostgresStore


DATABASE_URL = os.environ.get("ALCUIN_TEST_POSTGRES_URL")


def require_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    database_name = urlsplit(url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_test_database(DATABASE_URL)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="ALCUIN_TEST_POSTGRES_URL is required for PostgreSQL contract tests",
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


def add_workspace(repository: PostgresStore, workspace_id: str) -> None:
    with repository.connection:
        repository.connection.execute(
            "INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
            (workspace_id, workspace_id, "2026-08-28T00:00:00+00:00"),
        )


def definition(name: str) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {
            "identity": {"name": name, "description": "Contract verification"},
            "instructions": "Use only the supplied verification context.",
            "model": {"provider": "test", "model": "test-model"},
        }
    )


def create_run(repository: PostgresStore, workspace_id: str = "ws_demo") -> dict[str, Any]:
    agent = repository.list_agents(workspace_id)[0]
    thread = repository.create_thread(workspace_id, agent["id"], "Sequence test", {})
    return repository.create_run(
        workspace_id,
        thread["id"],
        agent["current_version_id"],
        "verify event ordering",
    )


def test_postgres_adapter_satisfies_control_plane_port(store: PostgresStore) -> None:
    assert isinstance(store, ControlPlaneRepository)
    assert store.workspace("ws_demo") is not None


def test_postgres_enforces_workspace_ownership_across_resources(
    store: PostgresStore,
) -> None:
    add_workspace(store, "ws_other")
    agent = store.create_agent(
        "ws_other",
        AgentCreate(slug="other-agent", definition=definition("Other agent")),
    )
    thread = store.create_thread("ws_other", agent["id"], "Other thread", {})
    run = store.create_run(
        "ws_other",
        thread["id"],
        agent["current_version_id"],
        "private prompt",
    )
    source = store.create_knowledge_source(
        "ws_other",
        KnowledgeSourceCreate(name="Other knowledge"),
    )
    extension = store.install_extension(
        "ws_other",
        ExtensionManifest.model_validate(
            {
                "id": "verification.storage",
                "name": "Storage verification",
                "version": "0.1.0",
                "entrypoints": [{"type": "builtin", "adapter": "verification"}],
            }
        ),
        {"api": "secret://workspace/other-api"},
    )
    event = store.append_event("ws_other", run["id"], "run.started", {})
    approval = store.create_approval(
        "ws_other",
        run["id"],
        {"tool": "verification.write"},
    )

    assert store.get_agent("ws_demo", agent["id"]) is None
    assert store.get_thread("ws_demo", thread["id"]) is None
    assert store.get_run("ws_demo", run["id"]) is None
    assert store.get_knowledge_source("ws_demo", source["id"]) is None
    assert store.get_extension("ws_demo", extension["id"]) is None
    assert store.list_events("ws_demo", run["id"]) == []
    assert store.get_approval("ws_demo", approval["id"]) is None
    assert event["sequence"] == 1
    assert store.list_extensions("ws_demo") == []
    assert store.list_runs("ws_demo") == []
    assert store.list_thread_runs("ws_demo", thread["id"]) == []
    assert [item["id"] for item in store.list_thread_runs("ws_other", thread["id"])] == [
        run["id"]
    ]


def test_postgres_normalizes_scoped_uniqueness_conflicts(
    store: PostgresStore,
) -> None:
    store.create_knowledge_source(
        "ws_demo",
        KnowledgeSourceCreate(name="Duplicate source"),
    )
    with pytest.raises(RepositoryConflict, match="already exists"):
        store.create_knowledge_source(
            "ws_demo",
            KnowledgeSourceCreate(name="Duplicate source"),
        )

    store.create_agent(
        "ws_demo",
        AgentCreate(slug="duplicate-agent", definition=definition("First")),
    )
    with pytest.raises(RepositoryConflict, match="already exists"):
        store.create_agent(
            "ws_demo",
            AgentCreate(slug="duplicate-agent", definition=definition("Second")),
        )


def test_agent_versions_are_immutable_hashes_and_publish_exactly(
    store: PostgresStore,
) -> None:
    first_definition = definition("Version integrity")
    agent = store.create_agent(
        "ws_demo",
        AgentCreate(slug="version-integrity", definition=first_definition),
    )
    first_version_id = agent["current_version_id"]
    first_version = store.get_agent_version("ws_demo", first_version_id)

    assert agent["status"] == "draft"
    assert agent["published_version_id"] is None
    assert first_version is not None
    assert first_version["published_at"] is None
    assert first_version["definition_sha256"] == hashlib.sha256(
        first_definition.model_dump_json().encode("utf-8")
    ).hexdigest()

    published_first = store.publish_agent_version(
        "ws_demo",
        agent["id"],
        first_version_id,
    )
    assert published_first is not None
    assert published_first["published_version_id"] == first_version_id
    published_version = store.get_agent_version("ws_demo", first_version_id)
    assert published_version is not None
    assert published_version["published_at"]

    second_definition = first_definition.model_copy(
        update={"instructions": "Use the second immutable instruction set only."}
    )
    current = store.create_agent_version(
        "ws_demo",
        agent["id"],
        second_definition,
    )
    assert current is not None
    second_version_id = current["current_version_id"]
    assert second_version_id != first_version_id
    assert current["status"] == "published"
    assert current["published_version_id"] == first_version_id
    assert [
        item["version"]
        for item in store.list_agent_versions("ws_demo", agent["id"])
    ] == [2, 1]

    persisted_first = store.get_agent_version("ws_demo", first_version_id)
    assert persisted_first is not None
    assert persisted_first["definition"] == first_definition.model_dump(mode="json")
    assert persisted_first["definition_sha256"] == first_version["definition_sha256"]

    published_second = store.publish_agent_version(
        "ws_demo",
        agent["id"],
        second_version_id,
    )
    assert published_second is not None
    assert published_second["published_version_id"] == second_version_id
    assert published_second["current_version_id"] == second_version_id


def test_thread_version_pin_is_workspace_scoped_and_controls_every_run(
    store: PostgresStore,
) -> None:
    agent = store.create_agent(
        "ws_demo",
        AgentCreate(slug="thread-pin", definition=definition("Thread pin")),
    )
    first_version_id = agent["current_version_id"]
    thread = store.create_thread(
        "ws_demo",
        agent["id"],
        "Pinned Thread",
        {},
        agent_version_id=first_version_id,
    )
    current = store.create_agent_version(
        "ws_demo",
        agent["id"],
        definition("Thread pin v2"),
    )
    assert current is not None
    second_version_id = current["current_version_id"]

    assert thread["agent_version_id"] == first_version_id
    persisted_thread = store.get_thread("ws_demo", thread["id"])
    assert persisted_thread is not None
    assert persisted_thread["agent_version_id"] == first_version_id
    with pytest.raises(RepositoryConflict, match="immutable Thread version"):
        store.create_run(
            "ws_demo",
            thread["id"],
            second_version_id,
            "Must not drift to v2",
        )
    run = store.create_run(
        "ws_demo",
        thread["id"],
        first_version_id,
        "Remain on v1",
    )
    assert run["agent_version_id"] == first_version_id

    add_workspace(store, "ws_other")
    other = store.create_agent(
        "ws_other",
        AgentCreate(slug="other-thread-pin", definition=definition("Other pin")),
    )
    with pytest.raises(RepositoryConflict, match="does not belong"):
        store.create_thread(
            "ws_demo",
            agent["id"],
            "Cross-workspace pin",
            {},
            agent_version_id=other["current_version_id"],
        )


def test_agent_version_numbers_are_serialized_across_store_instances(
    store: PostgresStore,
) -> None:
    assert DATABASE_URL is not None
    agent = store.create_agent(
        "ws_demo",
        AgentCreate(slug="concurrent-version", definition=definition("Version one")),
    )
    second = PostgresStore(DATABASE_URL, pool_max_size=2)

    def create(index: int) -> dict[str, Any] | None:
        repository = store if index == 0 else second
        return repository.create_agent_version(
            "ws_demo",
            agent["id"],
            definition(f"Concurrent version {index + 2}"),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(create, range(2)))
        assert sorted(
            item["version"]
            for item in store.list_agent_versions("ws_demo", agent["id"])
        ) == [1, 2, 3]
    finally:
        second.close()


def test_postgres_event_sequence_is_atomic_across_store_instances(
    store: PostgresStore,
) -> None:
    assert DATABASE_URL is not None
    second = PostgresStore(DATABASE_URL, pool_max_size=4)
    run = create_run(store)

    def append(index: int) -> dict[str, Any]:
        repository = store if index % 2 == 0 else second
        return repository.append_event(
            "ws_demo",
            run["id"],
            "message.delta",
            {"index": index},
        )

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            emitted = list(executor.map(append, range(64)))
        assert sorted(event["sequence"] for event in emitted) == list(range(1, 65))
        persisted = store.list_events("ws_demo", run["id"])
        assert [event["sequence"] for event in persisted] == list(range(1, 65))
    finally:
        second.close()


def test_run_creation_atomically_persists_one_workspace_scoped_user_message(
    store: PostgresStore,
) -> None:
    agent = store.list_agents("ws_demo")[0]
    thread = store.create_thread("ws_demo", agent["id"], "Conversation", {})

    run = store.create_run_with_messages(
        "ws_demo",
        thread["id"],
        agent["current_version_id"],
        "Remember this request",
        [{"type": "text", "text": "Remember this request"}],
        5,
    )

    messages = store.list_messages("ws_demo", thread["id"])
    refreshed_thread = store.get_thread("ws_demo", thread["id"])
    assert len(messages) == 1
    assert messages[0]["id"] == run["input_message_id"]
    assert messages[0]["parts"] == [
        {"type": "text", "text": "Remember this request"}
    ]
    assert messages[0]["role"] == "user"
    assert messages[0]["status"] == "completed"
    assert messages[0]["sequence"] == 1
    assert refreshed_thread is not None
    assert refreshed_thread["last_message_sequence"] == 1
    assert store.list_messages("ws_other", thread["id"]) == []


def test_run_creation_rejects_inline_data_and_a_second_active_thread_run(
    store: PostgresStore,
) -> None:
    assert DATABASE_URL is not None
    agent = store.list_agents("ws_demo")[0]
    thread = store.create_thread("ws_demo", agent["id"], "Exclusive run", {})

    with pytest.raises(ValueError, match="data URLs"):
        store.create_run_with_messages(
            "ws_demo",
            thread["id"],
            agent["current_version_id"],
            "Inspect",
            [{"type": "image", "data_url": "data:image/png;base64,AAAA"}],
            1,
        )
    assert store.list_runs("ws_demo") == []
    assert store.list_messages("ws_demo", thread["id"]) == []

    store.create_run(
        "ws_demo",
        thread["id"],
        agent["current_version_id"],
        "First",
    )
    second = PostgresStore(DATABASE_URL, pool_max_size=2)
    try:
        with pytest.raises(RepositoryConflict, match="active Run"):
            second.create_run(
                "ws_demo",
                thread["id"],
                agent["current_version_id"],
                "Second",
            )
    finally:
        second.close()


def test_assistant_finalization_is_atomic_idempotent_and_traceable_from_run(
    store: PostgresStore,
) -> None:
    run = create_run(store)
    message = store.finalize_assistant_message(
        "ws_demo",
        run["id"],
        "completed",
        "Final answer",
        3,
    )
    repeated = store.finalize_assistant_message(
        "ws_demo",
        run["id"],
        "completed",
        "Final answer",
        3,
    )

    refreshed = store.get_run("ws_demo", run["id"])
    assert repeated["id"] == message["id"]
    assert refreshed is not None
    assert refreshed["status"] == "completed"
    assert refreshed["output_message_id"] == message["id"]
    assert [row["role"] for row in store.list_messages("ws_demo", run["thread_id"])] == [
        "user",
        "assistant",
    ]
    with pytest.raises(RepositoryConflict, match="different immutable"):
        store.finalize_assistant_message(
            "ws_demo",
            run["id"],
            "completed",
            "Changed answer",
            3,
        )


def test_failed_run_may_persist_only_an_explicit_safe_partial_assistant_message(
    store: PostgresStore,
) -> None:
    run = create_run(store)

    message = store.finalize_assistant_message(
        "ws_demo",
        run["id"],
        "failed",
        "A safe partial response was produced before the provider failed.",
        12,
    )

    refreshed = store.get_run("ws_demo", run["id"])
    assert message["status"] == "failed"
    assert refreshed is not None
    assert refreshed["status"] == "failed"
    assert refreshed["output_message_id"] == message["id"]


def test_compaction_and_run_context_assembly_are_immutable_and_workspace_scoped(
    store: PostgresStore,
) -> None:
    history_run = create_run(store)
    store.finalize_assistant_message(
        "ws_demo", history_run["id"], "completed", "Answer", 2
    )
    history_messages = store.list_messages("ws_demo", history_run["thread_id"])
    compaction = store.create_compaction(
        "ws_demo",
        history_run["thread_id"],
        through_sequence=2,
        summary="The user requested event-order verification and received an answer.",
        source_message_ids=[row["id"] for row in history_messages],
        source_digest="a" * 64,
        estimated_source_tokens=9,
        estimated_summary_tokens=5,
        strategy="contract-summary-v1",
        created_by_run_id=history_run["id"],
    )
    run = store.create_run(
        "ws_demo",
        history_run["thread_id"],
        history_run["agent_version_id"],
        "verify the result again",
    )
    messages = store.list_messages("ws_demo", run["thread_id"])
    current_message = messages[-1]
    entries = [
        {
            "kind": "message",
            "label": "Current user message",
            "source_ref": current_message["id"],
            "token_estimate": 5,
            "included": True,
        }
    ]
    normalized = {
        "system_prompt": "Platform and Agent instructions",
        "messages": [{"role": "user", "content": "verify event ordering"}],
    }
    assembly = store.create_context_assembly(
        "ws_demo",
        run["id"],
        entries=entries,
        normalized_input=normalized,
        estimated_input_tokens=21,
        effective_budget_tokens=100,
        compaction_trigger_tokens=80,
        message_sequence_through=3,
        estimator_revision="contract-v1",
        active_compaction_id=compaction["id"],
    )
    repeated = store.create_context_assembly(
        "ws_demo",
        run["id"],
        entries=entries,
        normalized_input=normalized,
        estimated_input_tokens=21,
        effective_budget_tokens=100,
        compaction_trigger_tokens=80,
        message_sequence_through=3,
        estimator_revision="contract-v1",
        active_compaction_id=compaction["id"],
    )

    refreshed_run = store.get_run("ws_demo", run["id"])
    refreshed_thread = store.get_thread("ws_demo", run["thread_id"])
    assert repeated["id"] == assembly["id"]
    assert assembly["normalized_input"] == normalized
    assert assembly["entries"] == entries
    assert refreshed_run is not None
    assert refreshed_run["context_assembly_id"] == assembly["id"]
    assert refreshed_thread is not None
    assert refreshed_thread["active_compaction_id"] == compaction["id"]
    assert store.get_active_compaction("ws_other", run["thread_id"]) is None
    assert store.get_context_assembly("ws_other", run["id"]) is None

    with pytest.raises(RepositoryConflict, match="different immutable"):
        store.create_context_assembly(
            "ws_demo",
            run["id"],
            entries=entries,
            normalized_input={"system_prompt": "changed"},
            estimated_input_tokens=21,
            effective_budget_tokens=100,
            compaction_trigger_tokens=80,
            message_sequence_through=3,
            estimator_revision="contract-v1",
            active_compaction_id=compaction["id"],
        )


def test_startup_recovery_atomically_fails_only_interrupted_runs(store: PostgresStore) -> None:
    running = create_run(store)
    store.set_run_status("ws_demo", running["id"], "running")
    store.append_event("ws_demo", running["id"], "run.started", {})
    store.append_event(
        "ws_demo",
        running["id"],
        "reasoning.delta",
        {"delta": "private reasoning must not become assistant content"},
    )
    store.append_event(
        "ws_demo",
        running["id"],
        "tool.completed",
        {"result": {"secret": "tool output must not become assistant content"}},
    )
    store.append_event(
        "ws_demo", running["id"], "message.delta", {"delta": "Visible "}
    )
    store.append_event(
        "ws_demo", running["id"], "message.delta", {"delta": "partial"}
    )
    queued = create_run(store)
    waiting = create_run(store)
    store.set_run_status("ws_demo", waiting["id"], "waiting_for_approval")
    store.create_approval(
        "ws_demo",
        waiting["id"],
        {"tool": "verification.write", "arguments": {}},
    )

    recovered = store.recover_interrupted_runs()

    assert {item["run_id"] for item in recovered} == {running["id"], queued["id"]}
    running_after = store.get_run("ws_demo", running["id"])
    queued_after = store.get_run("ws_demo", queued["id"])
    waiting_after = store.get_run("ws_demo", waiting["id"])
    assert running_after is not None and running_after["status"] == "failed"
    assert queued_after is not None and queued_after["status"] == "failed"
    assert waiting_after is not None and waiting_after["status"] == "waiting_for_approval"

    running_messages = store.list_messages("ws_demo", running["thread_id"])
    assert [message["sequence"] for message in running_messages] == [1, 2]
    assistant = running_messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["status"] == "failed"
    assert assistant["parts"] == [{"type": "text", "text": "Visible partial"}]
    assert "private reasoning" not in str(assistant["parts"])
    assert "tool output" not in str(assistant["parts"])
    assert store.list_messages("ws_other", running["thread_id"]) == []

    queued_messages = store.list_messages("ws_demo", queued["thread_id"])
    assert len(queued_messages) == 1
    assert queued_messages[0]["role"] == "user"
    assert store.get_thread("ws_demo", queued["thread_id"])[
        "last_message_sequence"
    ] == 1

    failed_events = [
        event
        for event in store.list_events("ws_demo", running["id"])
        if event["type"] == "run.failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["payload"]["code"] == "runtime_interrupted"
    assert failed_events[0]["payload"]["automatic_retry"] is False
    assert (
        failed_events[0]["payload"]["external_tool_results"]
        == "verification_required"
    )
    assert failed_events[0]["payload"]["output_message_id"] == assistant["id"]
    assert not any(
        event["type"] == "run.failed"
        for event in store.list_events("ws_demo", waiting["id"])
    )

    assert store.recover_interrupted_runs() == []
    assert len(
        [
            event
            for event in store.list_events("ws_demo", running["id"])
            if event["type"] == "run.failed"
        ]
    ) == 1
    resumed = store.create_run(
        "ws_demo",
        running["thread_id"],
        running["agent_version_id"],
        "A new user-requested Run after recovery",
    )
    assert resumed["status"] == "queued"
