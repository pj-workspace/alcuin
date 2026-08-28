from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    REASONING_DELTA = "reasoning.delta"
    MESSAGE_DELTA = "message.delta"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUIRED = "approval.required"
    ARTIFACT_UPDATED = "artifact.updated"
    CITATION_CREATED = "citation.created"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


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
    runtime: RuntimeBinding = Field(default_factory=RuntimeBinding)
    policies: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "artifact"})
    starter_prompts: list[str] = Field(default_factory=list, max_length=6)


class AgentCreate(StrictModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    definition: AgentDefinition


class AgentVersionCreate(StrictModel):
    definition: AgentDefinition


class ThreadCreate(StrictModel):
    agent_id: str
    title: str | None = Field(default=None, max_length=120)
    context: dict[str, Any] = Field(default_factory=dict)


class ImageAttachment(StrictModel):
    type: Literal["image"] = "image"
    name: str = Field(min_length=1, max_length=180)
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data_url: str

    @model_validator(mode="after")
    def validate_data_url(self) -> "ImageAttachment":
        prefix = f"data:{self.media_type};base64,"
        if not self.data_url.startswith(prefix):
            raise ValueError("data_url must match the declared image media_type")
        try:
            payload = base64.b64decode(self.data_url[len(prefix) :], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("data_url must contain valid base64 image data") from exc
        if len(payload) > 5 * 1024 * 1024:
            raise ValueError("image attachments must be 5 MiB or smaller")
        return self


class RunCreate(StrictModel):
    input: str = Field(default="", max_length=40_000)
    attachments: list[ImageAttachment] = Field(default_factory=list, max_length=4)
    thinking: bool = True

    @model_validator(mode="after")
    def validate_content(self) -> "RunCreate":
        if not self.input.strip() and not self.attachments:
            raise ValueError("a run requires text or at least one attachment")
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


class ExtensionContributions(StrictModel):
    tools: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    agent_templates: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_connectors: list[dict[str, Any]] = Field(default_factory=list)
    ui_blocks: list[dict[str, Any]] = Field(default_factory=list)


class ExtensionManifest(StrictModel):
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
