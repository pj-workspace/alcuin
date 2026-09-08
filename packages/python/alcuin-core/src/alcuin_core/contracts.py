"""Stable, framework-neutral contracts shared across Alcuin packages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .customization import AgentRuleBinding, AgentSkillBinding


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    CONTEXT_COMPACTION_STARTED = "context.compaction.started"
    CONTEXT_COMPACTION_COMPLETED = "context.compaction.completed"
    CONTEXT_COMPACTION_FAILED = "context.compaction.failed"
    CONTEXT_ASSEMBLED = "context.assembled"
    REASONING_DELTA = "reasoning.delta"
    MESSAGE_DELTA = "message.delta"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUIRED = "approval.required"
    ARTIFACT_UPDATED = "artifact.updated"
    CITATION_CREATED = "citation.created"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class ReasoningEffort(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelBinding(StrictModel):
    provider: str = "openai-compatible"
    model: str = "gpt-4.1-mini"
    credential_ref: str | None = None

    @model_validator(mode="after")
    def validate_credential_ref(self) -> "ModelBinding":
        if self.credential_ref and not self.credential_ref.startswith("secret://"):
            raise ValueError("credential_ref must use the secret:// scheme")
        return self


class AgentIdentity(StrictModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=400)
    icon: str = "spark"


class RuntimeBinding(StrictModel):
    adapter: str = "langgraph-react"
    max_steps: int = Field(default=8, ge=1, le=32)


class ApprovalPolicy(StrictModel):
    mutating_tools: Literal["ask", "deny", "auto"] = "ask"
    external_side_effects: Literal["ask", "deny", "auto"] = "ask"


class AgentDefinition(StrictModel):
    schema_version: str = "2026-08-28"
    identity: AgentIdentity
    instructions: str = Field(min_length=8, max_length=20_000)
    model: ModelBinding = Field(default_factory=ModelBinding)
    extensions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    skills: list[AgentSkillBinding] = Field(default_factory=list, max_length=32)
    rules: list[AgentRuleBinding] = Field(default_factory=list, max_length=64)
    runtime: RuntimeBinding = Field(default_factory=RuntimeBinding)
    policies: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "artifact"})
    starter_prompts: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_customization_bindings(self) -> "AgentDefinition":
        skill_ids = [binding.skill_version_id for binding in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("Agent Skill version bindings must be unique")
        rule_ids = [binding.rule_version_id for binding in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Agent Rule version bindings must be unique")
        return self


class AgentCreate(StrictModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    definition: AgentDefinition


class AgentVersionCreate(StrictModel):
    definition: AgentDefinition


class ThreadCreate(StrictModel):
    agent_id: str
    agent_version_id: str | None = None
    title: str | None = Field(default=None, max_length=120)
    context: dict[str, Any] = Field(default_factory=dict)


class AttachmentDocumentMetadata(StrictModel):
    format: Literal["txt", "markdown", "pdf", "docx"]
    page_count: int | None = Field(default=None, ge=1)
    extracted_chars: int = Field(ge=1)


class AttachmentResource(StrictModel):
    id: str
    workspace_id: str
    name: str = Field(min_length=1, max_length=180)
    media_type: str = Field(min_length=1, max_length=160)
    kind: Literal["image", "document"]
    size_bytes: int = Field(gt=0)
    status: Literal["ready"] = "ready"
    document: AttachmentDocumentMetadata | None = None
    created_at: str
    expires_at: str

    @model_validator(mode="after")
    def validate_document_metadata(self) -> "AttachmentResource":
        if self.kind == "document" and self.document is None:
            raise ValueError("document attachments require document metadata")
        if self.kind == "image" and self.document is not None:
            raise ValueError("image attachments cannot include document metadata")
        return self


class ArtifactResource(StrictModel):
    """Latest public projection of a Workspace-scoped editable Artifact."""

    id: str
    workspace_id: str
    thread_id: str
    source_run_id: str
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(
        default="document",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    content_type: Literal["text/markdown", "text/plain", "application/json", "text/html"] = (
        "text/markdown"
    )
    version: int = Field(ge=1)
    content: str = Field(max_length=500_000)
    created_at: str
    updated_at: str


class ArtifactUpdate(StrictModel):
    """Optimistic edit against the latest Artifact projection."""

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=500_000)

    @model_validator(mode="after")
    def validate_changes(self) -> "ArtifactUpdate":
        if self.title is None and self.content is None:
            raise ValueError("an Artifact update requires title or content")
        if self.title is not None and not self.title.strip():
            raise ValueError("Artifact title cannot be blank")
        return self


class RequestedToolCall(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)
    extension_manifest_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    ui_block_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")

    @model_validator(mode="after")
    def validate_size(self) -> "RequestedToolCall":
        if len(json.dumps(self.arguments, ensure_ascii=False, default=str)) > 32_000:
            raise ValueError("requested tool arguments exceed 32000 characters")
        return self


class RunCreate(StrictModel):
    input: str = Field(default="", max_length=40_000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    thinking: bool | None = None
    model_override: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    reasoning_effort: ReasoningEffort | None = None
    requested_tool: RequestedToolCall | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "RunCreate":
        if not self.input.strip() and not self.attachment_ids and not self.requested_tool:
            raise ValueError("a run requires text, an attachment, or a requested tool")
        if self.requested_tool and self.attachment_ids:
            raise ValueError("requested tool runs cannot include attachments")
        if len(self.attachment_ids) != len(set(self.attachment_ids)):
            raise ValueError("attachment_ids must be unique")
        return self


class KnowledgeSourceCreate(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)


class KnowledgeDocumentCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2_000_000)
    source_uri: str | None = Field(default=None, max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_document(self) -> "KnowledgeDocumentCreate":
        if not self.content.strip():
            raise ValueError("knowledge document content cannot be blank")
        metadata_size = len(
            json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        if metadata_size > 16_384:
            raise ValueError("knowledge document metadata must be 16 KiB or smaller")
        return self


class ApprovalDecision(StrictModel):
    decision: Literal["approved", "denied"]
    note: str | None = Field(default=None, max_length=500)


class ExecutionEvent(StrictModel):
    id: str
    run_id: str
    sequence: int
    type: EventType
    timestamp: str
    payload: dict[str, Any]


class ThreadDetail(StrictModel):
    """Conversation log with a bounded recent-source projection, not full Run traces."""

    thread: dict[str, Any]
    messages: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    citation_events: list[ExecutionEvent] = Field(default_factory=list, max_length=6_400)

    @model_validator(mode="after")
    def validate_citation_scope(self) -> "ThreadDetail":
        run_ids = {run.get("id") for run in self.runs}
        if any(
            event.type != EventType.CITATION_CREATED or event.run_id not in run_ids
            for event in self.citation_events
        ):
            raise ValueError("Thread evidence must contain only this Thread's Run citations")
        return self


class PermissionSpec(StrictModel):
    id: str
    reason: str
    risk: Literal["low", "medium", "high"] = "low"
    required: bool = True


class MCPEntrypoint(StrictModel):
    type: Literal["mcp"] = "mcp"
    transport: Literal["stdio", "sse", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> "MCPEntrypoint":
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP entrypoints require command")
        if self.transport != "stdio" and not self.url:
            raise ValueError("remote MCP entrypoints require url")
        return self


class OpenAPIEntrypoint(StrictModel):
    type: Literal["openapi"] = "openapi"
    spec_url: HttpUrl | None = None
    base_url: HttpUrl | None = None
    auth: Literal["none", "api_key", "bearer"] = "none"


class BuiltinEntrypoint(StrictModel):
    type: Literal["builtin"] = "builtin"
    adapter: str


ExtensionEntrypoint = Annotated[
    MCPEntrypoint | OpenAPIEntrypoint | BuiltinEntrypoint,
    Field(discriminator="type"),
]


class UIBlockDataSource(StrictModel):
    kind: Literal["context", "artifact", "tool_result"]
    tool: str | None = Field(default=None, min_length=1, max_length=200)
    path: str = Field(default="", max_length=200, pattern=r"^[a-zA-Z0-9_.-]*$")

    @model_validator(mode="after")
    def validate_tool_source(self) -> "UIBlockDataSource":
        if any(
            segment in {"__proto__", "prototype", "constructor"}
            for segment in self.path.split(".")
        ):
            raise ValueError("UI data path contains a reserved segment")
        if self.kind == "tool_result" and not self.tool:
            raise ValueError("tool_result UI source requires a tool")
        if self.kind != "tool_result" and self.tool:
            raise ValueError("only tool_result UI sources may declare a tool")
        return self


class UIBlockValueField(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    path: str = Field(max_length=200, pattern=r"^[a-zA-Z0-9_.-]*$")
    format: Literal["text", "number", "status", "date", "json"] = "text"

    @model_validator(mode="after")
    def validate_path(self) -> "UIBlockValueField":
        if any(
            segment in {"__proto__", "prototype", "constructor"}
            for segment in self.path.split(".")
        ):
            raise ValueError("UI value path contains a reserved segment")
        return self


class UICardBlock(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    type: Literal["card"] = "card"
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    source: UIBlockDataSource
    fields: list[UIBlockValueField] = Field(min_length=1, max_length=12)


class UITableBlock(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    type: Literal["table"] = "table"
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    source: UIBlockDataSource
    columns: list[UIBlockValueField] = Field(min_length=1, max_length=12)
    empty_state: str = Field(default="No data available", max_length=160)


class UIFormField(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=80)
    input: Literal["text", "textarea", "number", "select"] = "text"
    required: bool = False
    placeholder: str = Field(default="", max_length=160)
    default_path: str = Field(default="", max_length=200, pattern=r"^[a-zA-Z0-9_.-]*$")
    options: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_options(self) -> "UIFormField":
        if any(
            segment in {"__proto__", "prototype", "constructor"}
            for segment in self.default_path.split(".")
        ):
            raise ValueError("UI default path contains a reserved segment")
        if self.input == "select" and not self.options:
            raise ValueError("select UI fields require options")
        if self.input != "select" and self.options:
            raise ValueError("only select UI fields may declare options")
        if len(set(self.options)) != len(self.options):
            raise ValueError("UI field options must be unique")
        return self


class UIFormSubmit(StrictModel):
    tool: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=80)


class UIFormBlock(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    type: Literal["form"] = "form"
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    fields: list[UIFormField] = Field(min_length=1, max_length=12)
    submit: UIFormSubmit

    @model_validator(mode="after")
    def validate_fields(self) -> "UIFormBlock":
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("UI form field names must be unique")
        return self


UIBlockContribution = Annotated[
    UICardBlock | UITableBlock | UIFormBlock,
    Field(discriminator="type"),
]


class ExtensionContributions(StrictModel):
    tools: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    agent_templates: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_connectors: list[dict[str, Any]] = Field(default_factory=list)
    ui_blocks: list[UIBlockContribution] = Field(default_factory=list, max_length=24)


class ExtensionManifest(StrictModel):
    schema_uri: str | None = Field(default=None, alias="$schema", exclude=True)
    manifest_version: Literal["1"] = "1"
    id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    name: str = Field(min_length=2, max_length=100)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$")
    description: str = Field(default="", max_length=500)
    compatibility: str = ">=0.1.0"
    contributions: ExtensionContributions = Field(default_factory=ExtensionContributions)
    entrypoints: list[ExtensionEntrypoint] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    permissions: list[PermissionSpec] = Field(default_factory=list)
    credential_requirements: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_extension_semantics(self) -> "ExtensionManifest":
        if not self.entrypoints:
            raise ValueError("extension requires at least one entrypoint")
        tool_names: set[str] = set()
        has_mutating_tool = False
        for tool in self.contributions.tools:
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("extension tool name is required")
            if name in tool_names:
                raise ValueError(f"duplicate extension tool name: {name}")
            tool_names.add(name)
            schema = tool.get("input_schema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError(
                    f"extension tool input_schema must be an object schema: {name}"
                )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ValueError(
                    f"extension tool input_schema is invalid: {name}"
                ) from exc
            if "mutating" in tool and not isinstance(tool["mutating"], bool):
                raise ValueError(f"extension tool mutating must be boolean: {name}")
            if tool.get("mutating"):
                has_mutating_tool = True
                if tool.get("approval") not in {None, "ask"}:
                    raise ValueError(
                        f"mutating extension tool must require approval: {name}"
                    )
        permission_ids = [permission.id for permission in self.permissions]
        if len(permission_ids) != len(set(permission_ids)):
            raise ValueError("extension permission ids must be unique")
        if has_mutating_tool and not any(
            permission.risk == "high" for permission in self.permissions
        ):
            raise ValueError(
                "mutating extension tools require a high-risk permission"
            )
        credential_ids: set[str] = set()
        for requirement in self.credential_requirements:
            requirement_id = requirement.get("id")
            requirement_type = requirement.get("type")
            if not isinstance(requirement_id, str) or not requirement_id.strip():
                raise ValueError("credential requirement id is required")
            if requirement_id in credential_ids:
                raise ValueError("credential requirement ids must be unique")
            credential_ids.add(requirement_id)
            if not isinstance(requirement_type, str) or not requirement_type.strip():
                raise ValueError("credential requirement type is required")
            if "required" in requirement and not isinstance(
                requirement["required"], bool
            ):
                raise ValueError("credential requirement required must be boolean")
        block_ids: set[str] = set()
        for block in self.contributions.ui_blocks:
            if block.id in block_ids:
                raise ValueError(f"duplicate UI block id: {block.id}")
            block_ids.add(block.id)
            if isinstance(block, (UICardBlock, UITableBlock)):
                source_tool = block.source.tool
                if source_tool and source_tool not in tool_names:
                    raise ValueError(
                        f"UI block references an undeclared tool: {source_tool}"
                    )
            if isinstance(block, UIFormBlock) and block.submit.tool not in tool_names:
                raise ValueError(
                    f"UI form references an undeclared tool: {block.submit.tool}"
                )
        return self


class ManifestInspectRequest(StrictModel):
    manifest: ExtensionManifest


class ExtensionInstallRequest(StrictModel):
    manifest: ExtensionManifest
    credential_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_secret_refs(self) -> "ExtensionInstallRequest":
        invalid = [value for value in self.credential_refs.values() if not value.startswith("secret://")]
        if invalid:
            raise ValueError("credentials must be secret:// references")
        return self


class ExtensionStatusUpdate(StrictModel):
    enabled: bool


class ExtensionCredentialBinding(StrictModel):
    credential_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_secret_refs(self) -> "ExtensionCredentialBinding":
        invalid = [
            value
            for value in self.credential_refs.values()
            if not value.startswith("secret://")
        ]
        if invalid:
            raise ValueError("credentials must be secret:// references")
        return self


class MCPToolCallRequest(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class OpenAPIImportRequest(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    extension_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    spec: dict[str, Any] | None = None
    spec_text: str | None = Field(default=None, max_length=2 * 1024 * 1024)
    spec_url: HttpUrl | None = None
    base_url: HttpUrl | None = None
    selected_operations: list[str] = Field(default_factory=list)
    auth: Literal["none", "api_key", "bearer"] = "none"

    @model_validator(mode="after")
    def validate_source(self) -> "OpenAPIImportRequest":
        if not self.spec and not self.spec_text and not self.spec_url:
            raise ValueError("OpenAPI import requires spec, spec_text, or spec_url")
        return self


class MCPImportRequest(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    extension_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    version: str = Field(
        default="0.1.0",
        pattern=r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$",
    )
    description: str = Field(default="", max_length=500)
    entrypoint: MCPEntrypoint
    selected_tools: list[str] = Field(default_factory=list)


class EmbedSessionCreate(StrictModel):
    agent_id: str
    origin: str = Field(pattern=r"^https?://")
    ttl_seconds: int = Field(default=900, ge=60, le=3600)
    allowed_actions: list[str] = Field(default_factory=lambda: ["thread:create", "run:create", "run:read"])


class EmbedClaims(StrictModel):
    workspace_id: str
    agent_id: str
    agent_version_id: str
    origin: str
    allowed_actions: list[str]
    issued_at: int
    expires_at: int


class HealthReport(StrictModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    checked_at: str = Field(default_factory=utc_now)
    details: dict[str, Any] = Field(default_factory=dict)
