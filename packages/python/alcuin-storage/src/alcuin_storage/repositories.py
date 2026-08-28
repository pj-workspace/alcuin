"""Structural persistence ports consumed by Alcuin services.

The ports deliberately expose platform records rather than SQL rows or ORM models. Adapters
must enforce Workspace ownership on every resource lookup and mutation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
)

Record = dict[str, Any]


@runtime_checkable
class RuntimeRepository(Protocol):
    """Persistence required while executing and resuming a Run."""

    def get_run(self, workspace_id: str, run_id: str) -> Record | None: ...

    def get_agent_version(self, workspace_id: str, version_id: str) -> Record | None: ...

    def get_thread(self, workspace_id: str, thread_id: str) -> Record | None: ...

    def set_run_status(self, workspace_id: str, run_id: str, status: str) -> None: ...

    def append_event(
        self,
        workspace_id: str,
        run_id: str,
        event_type: str,
        payload: Record,
    ) -> Record: ...

    def create_approval(
        self,
        workspace_id: str,
        run_id: str,
        request: Record,
    ) -> Record: ...


@runtime_checkable
class ExtensionRepository(Protocol):
    """Workspace-owned Extension lifecycle and runtime resolution."""

    def list_extensions(self, workspace_id: str) -> list[Record]: ...

    def get_extension(self, workspace_id: str, extension_id: str) -> Record | None: ...

    def install_extension(
        self,
        workspace_id: str,
        manifest: ExtensionManifest,
        credential_refs: dict[str, str],
    ) -> Record: ...

    def update_extension(
        self,
        workspace_id: str,
        extension_id: str,
        *,
        status: str | None = None,
        health: str | None = None,
    ) -> Record | None: ...

    def update_extension_credentials(
        self,
        workspace_id: str,
        extension_id: str,
        credential_refs: dict[str, str],
    ) -> Record | None: ...

    def update_extension_manifest(
        self,
        workspace_id: str,
        extension_id: str,
        manifest: ExtensionManifest,
    ) -> Record | None: ...


@runtime_checkable
class KnowledgeRepository(Protocol):
    """Knowledge source and document lifecycle metadata."""

    def create_knowledge_source(
        self,
        workspace_id: str,
        payload: KnowledgeSourceCreate,
    ) -> Record: ...

    def list_knowledge_sources(self, workspace_id: str) -> list[Record]: ...

    def get_knowledge_source(self, workspace_id: str, source_id: str) -> Record | None: ...

    def begin_knowledge_document(
        self,
        workspace_id: str,
        source_id: str,
        *,
        title: str,
        source_uri: str | None,
        content: str,
        content_hash: str,
        index_revision: str,
        metadata: Record,
    ) -> tuple[Record, bool]: ...

    def finish_knowledge_document(
        self,
        workspace_id: str,
        document_id: str,
        *,
        chunk_count: int,
    ) -> Record | None: ...

    def fail_knowledge_document(
        self,
        workspace_id: str,
        document_id: str,
        *,
        error: str,
    ) -> None: ...

    def get_knowledge_document(
        self,
        workspace_id: str,
        document_id: str,
    ) -> Record | None: ...

    def list_knowledge_documents(
        self,
        workspace_id: str,
        source_id: str,
    ) -> list[Record]: ...

    def delete_knowledge_source(self, workspace_id: str, source_id: str) -> bool: ...

    def knowledge_source_references(
        self,
        workspace_id: str,
        source_id: str,
    ) -> list[Record]: ...


@runtime_checkable
class ControlPlaneRepository(RuntimeRepository, ExtensionRepository, KnowledgeRepository, Protocol):
    """Complete persistence surface composed by the API application."""

    def close(self) -> None: ...

    def workspace(self, workspace_id: str) -> Record | None: ...

    def list_agents(self, workspace_id: str) -> list[Record]: ...

    def get_agent(self, workspace_id: str, agent_id: str) -> Record | None: ...

    def create_agent(self, workspace_id: str, payload: AgentCreate) -> Record: ...

    def create_agent_version(
        self,
        workspace_id: str,
        agent_id: str,
        definition: AgentDefinition,
    ) -> Record | None: ...

    def publish_agent(self, workspace_id: str, agent_id: str) -> Record | None: ...

    def create_thread(
        self,
        workspace_id: str,
        agent_id: str,
        title: str,
        context: Record,
    ) -> Record: ...

    def list_threads(self, workspace_id: str) -> list[Record]: ...

    def create_run(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
    ) -> Record: ...

    def list_runs(self, workspace_id: str, limit: int = 30) -> list[Record]: ...

    def list_events(
        self,
        workspace_id: str,
        run_id: str,
        after: int = 0,
    ) -> list[Record]: ...

    def get_approval(self, workspace_id: str, approval_id: str) -> Record | None: ...

    def decide_approval(
        self,
        workspace_id: str,
        approval_id: str,
        decision: str,
        note: str | None,
    ) -> Record | None: ...

    def bootstrap(self, workspace_id: str) -> Record | None: ...
