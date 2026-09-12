"""Structural persistence ports consumed by Alcuin services.

The ports deliberately expose platform records rather than SQL rows or ORM models. Adapters
must enforce Workspace ownership on every resource lookup and mutation.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    ExtensionManifest,
    KnowledgeSourceCreate,
)
from alcuin_core.customization import (
    AgentRuleBinding,
    AgentSkillBinding,
    PreferenceUpdate,
    RuleCreate,
    RulePatch,
    RuleVersionCreate,
    SkillCreate,
    SkillPatch,
    SkillVersionCreate,
    ThreadConfigurationUpdate,
)

Record = dict[str, Any]


class SkillSummary(TypedDict):
    """Safe, lightweight Skill projection for list surfaces."""

    id: str
    workspace_id: str
    slug: str
    enabled: bool
    current_version_id: str
    definition_sha256: str
    source_kind: Literal["native", "agent_plugin", "cursor_plugin"]
    name: str
    display_name: str
    description: str
    disable_model_invocation: bool
    user_invocable: bool
    required_tools: list[str]
    resource_count: int
    created_at: str
    updated_at: str


class RuleSummary(TypedDict):
    """Safe, lightweight Rule projection for list surfaces."""

    id: str
    workspace_id: str
    slug: str
    scope: Literal["workspace", "thread", "library"]
    thread_id: str | None
    enabled: bool
    current_version_id: str
    definition_sha256: str
    source_kind: Literal["native", "agent_plugin", "cursor_plugin"]
    name: str
    description: str
    activation: Literal["always", "conditional", "manual"]
    priority: int
    condition_count: int
    created_at: str
    updated_at: str


@runtime_checkable
class ConversationRepository(Protocol):
    """Immutable Thread messages, compactions, and per-Run context snapshots."""

    def list_messages(
        self,
        workspace_id: str,
        thread_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[Record]: ...

    def get_message(self, workspace_id: str, message_id: str) -> Record | None: ...

    def list_thread_runs(
        self,
        workspace_id: str,
        thread_id: str,
        limit: int = 500,
    ) -> list[Record]: ...

    def list_thread_citation_events(
        self,
        workspace_id: str,
        thread_id: str,
        run_ids: list[str],
    ) -> list[Record]: ...

    def list_run_citation_events(
        self,
        workspace_id: str,
        run_id: str,
    ) -> list[Record]: ...

    def create_run_with_messages(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
        parts: list[Record],
        estimated_tokens: int,
        *,
        attachment_ids: tuple[str, ...] = (),
        max_total_attachment_bytes: int = 20 * 1024 * 1024,
    ) -> Record: ...

    def finalize_assistant_message(
        self,
        workspace_id: str,
        run_id: str,
        status: str,
        content: str,
        estimated_tokens: int,
    ) -> Record: ...

    def finalize_run_with_event(
        self,
        workspace_id: str,
        run_id: str,
        status: Literal["completed", "failed"],
        content: str,
        estimated_tokens: int,
        terminal_event_type: Literal["run.completed", "run.failed"],
        terminal_payload: Record,
    ) -> Record: ...

    def create_compaction(
        self,
        workspace_id: str,
        thread_id: str,
        **payload: Any,
    ) -> Record: ...

    def get_active_compaction(
        self,
        workspace_id: str,
        thread_id: str,
    ) -> Record | None: ...

    def create_context_assembly(
        self,
        workspace_id: str,
        run_id: str,
        **payload: Any,
    ) -> Record: ...

    def get_context_assembly(
        self,
        workspace_id: str,
        run_id: str,
    ) -> Record | None: ...

    def get_run_customization_snapshot(
        self,
        workspace_id: str,
        run_id: str,
    ) -> Record | None: ...


@runtime_checkable
class AttachmentRepository(Protocol):
    """Workspace-scoped staged blobs and immutable message bindings."""

    def create_attachment(
        self,
        workspace_id: str,
        *,
        upload_id: str,
        name: str,
        media_type: str,
        kind: Literal["image", "document"],
        content: bytes,
        sha256: str,
        expires_at: str,
        extracted_text: str | None = None,
        metadata: Record | None = None,
    ) -> Record: ...

    def get_attachment(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> Record | None: ...

    def get_attachment_blob(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> Record | None: ...

    def list_message_attachments(
        self,
        workspace_id: str,
        message_id: str,
    ) -> list[Record]: ...

    def delete_attachment(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> bool: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    """Workspace-scoped generated documents with optimistic user edits."""

    def append_artifact_event(
        self,
        workspace_id: str,
        run_id: str,
        artifact: Record,
    ) -> Record: ...

    def get_artifact(
        self,
        workspace_id: str,
        artifact_id: str,
    ) -> Record | None: ...

    def list_thread_artifacts(
        self,
        workspace_id: str,
        thread_id: str,
        *,
        limit: int = 50,
    ) -> list[Record]: ...

    def update_artifact(
        self,
        workspace_id: str,
        artifact_id: str,
        *,
        expected_version: int,
        title: str | None = None,
        content: str | None = None,
    ) -> Record | None: ...


@runtime_checkable
class RuntimeRepository(
    ConversationRepository,
    AttachmentRepository,
    ArtifactRepository,
    Protocol,
):
    """Persistence required while executing and resuming a Run."""

    def recover_interrupted_runs(self) -> list[Record]: ...

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
class CustomizationRepository(Protocol):
    """Workspace-owned Skills, Rules, and optimistic next-turn customization."""

    def create_skill(self, workspace_id: str, payload: SkillCreate) -> Record: ...

    def list_skills(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
    ) -> list[Record]: ...

    def list_skill_summaries(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SkillSummary]: ...

    def get_skill(self, workspace_id: str, skill_id: str) -> Record | None: ...

    def get_skill_version(
        self,
        workspace_id: str,
        skill_version_id: str,
    ) -> Record | None: ...

    def list_skill_versions(
        self,
        workspace_id: str,
        skill_id: str,
    ) -> list[Record]: ...

    def create_skill_version(
        self,
        workspace_id: str,
        skill_id: str,
        payload: SkillVersionCreate,
    ) -> Record | None: ...

    def update_skill(
        self,
        workspace_id: str,
        skill_id: str,
        payload: SkillPatch,
    ) -> Record | None: ...

    def list_bound_skill_versions(
        self,
        workspace_id: str,
        agent_version_id: str,
        *,
        enabled_only: bool = True,
    ) -> list[Record]: ...

    def get_bound_skill_version(
        self,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
    ) -> Record | None: ...

    def create_rule(self, workspace_id: str, payload: RuleCreate) -> Record: ...

    def list_rules(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
        scope: str | None = None,
        thread_id: str | None = None,
    ) -> list[Record]: ...

    def list_rule_summaries(
        self,
        workspace_id: str,
        *,
        enabled_only: bool = False,
        scope: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RuleSummary]: ...

    def get_rule(self, workspace_id: str, rule_id: str) -> Record | None: ...

    def get_rule_version(
        self,
        workspace_id: str,
        rule_version_id: str,
    ) -> Record | None: ...

    def list_rule_versions(
        self,
        workspace_id: str,
        rule_id: str,
    ) -> list[Record]: ...

    def create_rule_version(
        self,
        workspace_id: str,
        rule_id: str,
        payload: RuleVersionCreate,
    ) -> Record | None: ...

    def update_rule(
        self,
        workspace_id: str,
        rule_id: str,
        payload: RulePatch,
    ) -> Record | None: ...

    def install_customization_bundle(
        self,
        workspace_id: str,
        *,
        skills: list[SkillCreate],
        rules: list[RuleCreate],
    ) -> Record: ...

    def get_agent_version_customization_bindings(
        self,
        workspace_id: str,
        agent_version_id: str,
    ) -> Record | None: ...

    def set_agent_version_customization_bindings(
        self,
        workspace_id: str,
        agent_version_id: str,
        *,
        skills: list[AgentSkillBinding],
        rules: list[AgentRuleBinding],
    ) -> Record | None: ...

    def get_thread_configuration(
        self,
        workspace_id: str,
        thread_id: str,
    ) -> Record | None: ...

    def update_thread_configuration(
        self,
        workspace_id: str,
        thread_id: str,
        payload: ThreadConfigurationUpdate,
    ) -> Record | None: ...

    def get_workspace_preferences(self, workspace_id: str) -> Record | None: ...

    def update_workspace_preferences(
        self,
        workspace_id: str,
        payload: PreferenceUpdate,
    ) -> Record | None: ...


@runtime_checkable
class TaskRepository(Protocol):
    """Workspace-owned durable Tasks with explicit compare-and-swap transitions.

    A Task may span multiple Runs. Callers cannot write a status directly: every state
    change goes through a named transition that atomically records a Task event and a
    recovery checkpoint.
    """

    def create_task(
        self,
        workspace_id: str,
        thread_id: str,
        goal: str,
        *,
        title: str | None = None,
        metadata: Record | None = None,
        model_override: str | None = None,
        reasoning_effort: str | None = None,
    ) -> Record: ...

    def get_task(self, workspace_id: str, task_id: str) -> Record | None: ...

    def list_tasks(
        self,
        workspace_id: str,
        *,
        thread_id: str | None = None,
        statuses: tuple[str, ...] = (),
        limit: int = 50,
    ) -> list[Record]: ...

    def replace_task_plan(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        steps: list[Record],
        goal: str | None = None,
    ) -> Record: ...

    def get_task_command(
        self,
        workspace_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> Record | None: ...

    def execute_task_command(
        self,
        workspace_id: str,
        task_id: str,
        command: Record,
        *,
        include_replay_metadata: bool = False,
    ) -> Record: ...

    def start_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
    ) -> Record: ...

    def start_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        input: Record | None = None,
    ) -> Record: ...

    def complete_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        output: Record | None = None,
        evidence: list[Record] | None = None,
    ) -> Record: ...

    def fail_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        error: Record,
    ) -> Record: ...

    def complete_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        result: Record | None = None,
    ) -> Record: ...

    def fail_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        error: Record,
    ) -> Record: ...

    def request_task_pause(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Record: ...

    def mark_task_paused(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Record: ...

    def resume_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
    ) -> Record: ...

    def decide_task_approval(
        self,
        workspace_id: str,
        approval_id: str,
        decision: str,
        note: str | None,
    ) -> Record | None: ...

    def claim_task_dispatch(
        self,
        workspace_id: str,
        task_id: str,
        lease_owner: str,
        *,
        lease_seconds: int = 30,
    ) -> bool: ...

    def renew_task_dispatch(
        self,
        workspace_id: str,
        task_id: str,
        lease_owner: str,
        *,
        lease_seconds: int = 30,
    ) -> bool: ...

    def wake_task_dispatch(self, workspace_id: str, task_id: str) -> bool: ...

    def request_task_cancel(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Record: ...

    def mark_task_cancelled(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Record: ...

    def suspend_task(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        reason: Literal["approval", "user"],
        reference_id: str | None = None,
        payload: Record | None = None,
    ) -> Record: ...

    def mark_task_waiting_for_approval(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        approval_id: str,
        *,
        expected_revision: int,
    ) -> Record: ...

    def mark_task_waiting_for_user(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
        prompt: Record | None = None,
    ) -> Record: ...

    def intervene_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        expected_revision: int,
        kind: Literal["queue", "steer", "interrupt"],
        content: str,
        step_id: str | None = None,
        payload: Record | None = None,
    ) -> Record: ...

    def list_task_interventions(
        self,
        workspace_id: str,
        task_id: str,
        *,
        pending_only: bool = False,
    ) -> list[Record]: ...

    def apply_task_intervention(
        self,
        workspace_id: str,
        task_id: str,
        intervention_id: str,
        *,
        expected_revision: int,
    ) -> Record: ...

    def retry_task_step(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        *,
        expected_revision: int,
    ) -> Record: ...

    def link_task_run(
        self,
        workspace_id: str,
        task_id: str,
        step_id: str,
        attempt_id: str,
        run_id: str,
    ) -> Record: ...

    def get_task_run_link(
        self,
        workspace_id: str,
        run_id: str,
    ) -> Record | None: ...

    def list_task_events(
        self,
        workspace_id: str,
        task_id: str,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> list[Record]: ...

    def list_task_dispatches(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Record]: ...

    def recover_nonterminal_tasks(self) -> list[Record]: ...


@runtime_checkable
class ControlPlaneRepository(
    RuntimeRepository,
    ExtensionRepository,
    KnowledgeRepository,
    CustomizationRepository,
    TaskRepository,
    Protocol,
):
    """Complete persistence surface composed by the API application."""

    def close(self) -> None: ...

    def workspace(self, workspace_id: str) -> Record | None: ...

    def list_agents(self, workspace_id: str) -> list[Record]: ...

    def get_agent(self, workspace_id: str, agent_id: str) -> Record | None: ...

    def get_agent_version(
        self,
        workspace_id: str,
        version_id: str,
    ) -> Record | None: ...

    def list_agent_versions(
        self,
        workspace_id: str,
        agent_id: str,
    ) -> list[Record]: ...

    def create_agent(self, workspace_id: str, payload: AgentCreate) -> Record: ...

    def create_agent_version(
        self,
        workspace_id: str,
        agent_id: str,
        definition: AgentDefinition,
    ) -> Record | None: ...

    def publish_agent(self, workspace_id: str, agent_id: str) -> Record | None: ...

    def publish_agent_version(
        self,
        workspace_id: str,
        agent_id: str,
        version_id: str,
    ) -> Record | None: ...

    def create_thread(
        self,
        workspace_id: str,
        agent_id: str,
        title: str,
        context: Record,
        *,
        agent_version_id: str | None = None,
    ) -> Record: ...

    def list_threads(self, workspace_id: str) -> list[Record]: ...

    def thread_title_candidates(self, workspace_id: str, thread_id: str) -> list[Record]: ...

    def claim_thread_title(self, workspace_id: str, thread_id: str, expected_title: str, claim: str) -> bool: ...

    def finish_thread_title(self, workspace_id: str, thread_id: str, expected_title: str, claim: str, title: str | None) -> bool: ...

    def create_run(
        self,
        workspace_id: str,
        thread_id: str,
        agent_version_id: str,
        prompt: str,
        *,
        message_parts: list[Record] | None = None,
        estimated_tokens: int | None = None,
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
