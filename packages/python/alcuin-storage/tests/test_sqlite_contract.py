from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
)
from alcuin_storage import ControlPlaneRepository, RepositoryConflict, SqliteStore


def add_workspace(store: SqliteStore, workspace_id: str) -> None:
    with store.connection:
        store.connection.execute(
            "INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
            (workspace_id, workspace_id, "2026-08-28T00:00:00+00:00"),
        )


def test_sqlite_adapter_satisfies_control_plane_port() -> None:
    store = SqliteStore(":memory:")
    try:
        assert isinstance(store, ControlPlaneRepository)
        assert store.workspace("ws_demo") is not None
    finally:
        store.close()


def test_sqlite_adapter_enforces_workspace_ownership_across_resource_groups() -> None:
    store = SqliteStore(":memory:")
    add_workspace(store, "ws_other")
    try:
        definition = AgentDefinition.model_validate(
            {
                "identity": {"name": "Other agent", "description": "Owned elsewhere"},
                "instructions": "Use only supplied context.",
                "model": {"provider": "test", "model": "test-model"},
            }
        )
        agent = store.create_agent(
            "ws_other",
            AgentCreate(slug="other-agent", definition=definition),
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
        assert store.list_agents("ws_demo")[0]["id"] == "agt_starter"
        assert store.list_extensions("ws_demo") == []
        assert store.list_runs("ws_demo") == []
    finally:
        store.close()


def test_workspace_scope_is_required_by_foreign_keys() -> None:
    store = SqliteStore(":memory:")
    try:
        definition = AgentDefinition.model_validate(
            {
                "identity": {"name": "Invalid", "description": "No workspace"},
                "instructions": "Do nothing.",
                "model": {"provider": "test", "model": "test-model"},
            }
        )
        try:
            store.create_agent(
                "ws_missing",
                AgentCreate(slug="invalid", definition=definition),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("adapter accepted a resource without an owning Workspace")
    finally:
        store.close()


def test_adapter_normalizes_scoped_uniqueness_conflicts() -> None:
    store = SqliteStore(":memory:")
    try:
        store.create_knowledge_source(
            "ws_demo",
            KnowledgeSourceCreate(name="Duplicate source"),
        )
        try:
            store.create_knowledge_source(
                "ws_demo",
                KnowledgeSourceCreate(name="Duplicate source"),
            )
        except RepositoryConflict as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("adapter leaked or ignored a uniqueness conflict")
    finally:
        store.close()


def create_run(store: SqliteStore) -> dict:
    agent = store.get_agent("ws_demo", "agt_starter")
    assert agent is not None
    thread = store.create_thread("ws_demo", agent["id"], "Sequence test", {})
    return store.create_run(
        "ws_demo",
        thread["id"],
        agent["current_version_id"],
        "verify event ordering",
    )


def test_event_sequence_is_atomic_across_independent_connections(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "shared.db")
    first = SqliteStore(database)
    second = SqliteStore(database)
    run = create_run(first)

    def append(index: int) -> dict:
        store = first if index % 2 == 0 else second
        return store.append_event(
            "ws_demo",
            run["id"],
            "message.delta",
            {"index": index},
        )

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            emitted = list(executor.map(append, range(64)))

        assert sorted(event["sequence"] for event in emitted) == list(range(1, 65))
        persisted = first.list_events("ws_demo", run["id"])
        assert [event["sequence"] for event in persisted] == list(range(1, 65))
        assert {event["payload"]["index"] for event in persisted} == set(range(64))
    finally:
        second.close()
        first.close()


def test_existing_event_sequence_is_backfilled_on_open(tmp_path: Path) -> None:
    database = str(tmp_path / "migration.db")
    store = SqliteStore(database)
    run = create_run(store)
    store.append_event("ws_demo", run["id"], "run.started", {})
    store.append_event("ws_demo", run["id"], "message.delta", {"delta": "first"})
    with store.connection:
        store.connection.execute(
            "UPDATE runs SET next_event_sequence = 0 WHERE id = ?",
            (run["id"],),
        )
    store.close()

    reopened = SqliteStore(database)
    try:
        event = reopened.append_event("ws_demo", run["id"], "run.completed", {})
        assert event["sequence"] == 3
    finally:
        reopened.close()
