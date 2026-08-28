from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="ALCUIN_TEST_POSTGRES_URL is required for PostgreSQL contract tests",
)


def reset_database() -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """TRUNCATE TABLE
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
