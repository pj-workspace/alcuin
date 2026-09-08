from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from alcuin_knowledge import (
    DocumentParseError,
    DocumentParser,
    DocumentTooLargeError,
    FileDocumentParser,
    KnowledgeService,
    QdrantKnowledgeIndex,
    UnsupportedDocumentError,
)
from alcuin_storage import (
    ArtifactVersionConflict,
    AttachmentBindingError,
    ControlPlaneRepository,
    RepositoryConflict,
    RuleSummary,
    SkillSummary,
    open_repository,
)
from alcuin_customization import PluginArchiveError, inspect_plugin_archive
from alcuin_customization.plugin_import import MAX_ARCHIVE_BYTES
from alcuin_web_search import WebSearchService, is_public_http_url
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import Settings, get_settings
from .attachments import AttachmentService, AttachmentUploadError, public_attachment
from alcuin_core.contracts import (
    AgentCreate,
    AgentDefinition,
    AgentVersionCreate,
    ArtifactResource,
    ArtifactUpdate,
    AttachmentResource,
    ApprovalDecision,
    BuiltinEntrypoint,
    EmbedClaims,
    EmbedSessionCreate,
    EventType,
    ExtensionCredentialBinding,
    ExtensionInstallRequest,
    ExtensionManifest,
    ExtensionStatusUpdate,
    KnowledgeDocumentCreate,
    KnowledgeSourceCreate,
    ManifestInspectRequest,
    MCPEntrypoint,
    MCPImportRequest,
    MCPToolCallRequest,
    OpenAPIImportRequest,
    OpenAPIEntrypoint,
    ReasoningEffort,
    RequestedToolCall,
    RunCreate,
    ThreadCreate,
    ThreadDetail,
)
from alcuin_core.customization import (
    PreferenceUpdate,
    RuleCreate,
    RulePatch,
    SkillCreate,
    SkillPatch,
    ThreadConfigurationUpdate,
)
from .extensions import (
    check_extension_health,
    inspect_manifest,
    manifest_from_mcp,
    manifest_from_openapi,
    missing_credential_ids,
    refresh_mcp_manifest,
    resolve_openapi_document,
)
from .extension_tools import (
    BuiltinToolAdapter,
    ExtensionToolService,
    extension_tool_name,
)
from .knowledge_wiring import (
    document_limits,
    knowledge_config,
    knowledge_tool_definition,
)
from .mcp_gateway import MCPGateway
from .openapi_gateway import OpenAPIGateway
from .plugin_install import (
    PLUGIN_INSPECTION_POLICY_REVISION,
    PluginInspectionReceiptError,
    customization_bundle_draft,
    issue_plugin_inspection_receipt,
    verify_plugin_inspection_receipt,
)
from .runtime import RuntimeAttachmentRef, RuntimeOrchestrator, RuntimeRequest
from .context_composition import (
    load_thread_messages,
    persisted_user_parts,
    public_context_assembly,
)
from .security import RequestScope, issue_embed_token, resolve_scope
from .chat_sse import project_execution_event
from .tools import ToolExecutor, ToolRegistry
from .tasks import (
    RuntimeTaskStepRunner,
    TaskCoordinator,
    TaskService,
    create_task_router,
)
from .skill_wiring import skill_tool_definitions
from .web_search_wiring import web_search_config, web_search_tool_definition
from .run_profile import resolve_run_model_controls
from .artifact_downloads import export_artifact


ScopeDependency = Annotated[RequestScope, Depends(resolve_scope)]
BUILTIN_TOOL_IDS = frozenset({"web.search", "knowledge.search"})


def resolve_event_cursor(after: int, last_event_id: str | None) -> int:
    """Resolve the persisted event sequence used for an SSE resume."""
    if last_event_id is None:
        return after
    normalized = last_event_id.strip()
    if not normalized or not normalized.isascii() or not normalized.isdecimal():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative event sequence",
        )
    return max(after, int(normalized))


def create_app(
    settings: Settings | None = None,
    store: ControlPlaneRepository | None = None,
    knowledge_service: KnowledgeService | None = None,
    document_parser: DocumentParser | None = None,
    mcp_gateway: MCPGateway | None = None,
    openapi_gateway: OpenAPIGateway | None = None,
    builtin_adapters: Mapping[str, BuiltinToolAdapter] | None = None,
    provider_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    repository = store or open_repository(
        database_url=settings.database_url,
        postgres_pool_min_size=settings.postgres_pool_min_size,
        postgres_pool_max_size=settings.postgres_pool_max_size,
        postgres_pool_timeout_seconds=settings.postgres_pool_timeout_seconds,
    )
    web_search_service = (
        WebSearchService(web_search_config(settings))
        if (settings.searxng_url or "").strip()
        else None
    )
    configured_knowledge_service = knowledge_service or (
        KnowledgeService(repository, QdrantKnowledgeIndex(knowledge_config(settings)))
        if (settings.qdrant_url or "").strip()
        and (settings.dashscope_api_key or "").strip()
        else None
    )
    configured_document_parser = document_parser or FileDocumentParser(
        document_limits(settings)
    )
    attachment_service = AttachmentService(repository, settings)
    configured_mcp_gateway = mcp_gateway or MCPGateway()
    configured_openapi_gateway = openapi_gateway or OpenAPIGateway(
        timeout_seconds=settings.extension_health_timeout_seconds
    )
    definitions = list(skill_tool_definitions(repository))
    if web_search_service:
        definitions.append(web_search_tool_definition(web_search_service))
    if configured_knowledge_service:
        definitions.append(knowledge_tool_definition(configured_knowledge_service))
    tool_registry = ToolRegistry(definitions)
    extension_tool_service = ExtensionToolService(
        repository,
        configured_mcp_gateway,
        configured_openapi_gateway,
        builtin_adapters,
    )
    tool_executor = ToolExecutor(
        tool_registry,
        dynamic_resolver=extension_tool_service.definitions,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.tasks = set()
        application.state.recovered_interrupted_runs = (
            repository.recover_interrupted_runs()
        )
        application.state.recovered_tasks = application.state.task_coordinator.recover()
        yield
        await application.state.task_coordinator.close()
        for task in application.state.tasks:
            task.cancel()
        if web_search_service:
            await web_search_service.aclose()
        if configured_knowledge_service:
            await configured_knowledge_service.aclose()
        if store is None:
            repository.close()

    app = FastAPI(
        title="Alcuin API",
        version="0.1.0",
        description="Workspace-aware control plane and runtime gateway for composable agents.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = repository
    app.state.settings = settings
    app.state.web_search_service = web_search_service
    app.state.knowledge_service = configured_knowledge_service
    app.state.attachment_service = attachment_service
    app.state.extension_tool_service = extension_tool_service
    app.state.runtime = RuntimeOrchestrator(
        repository,
        settings,
        tool_executor,
        provider_transport=provider_transport,
    )
    app.state.task_coordinator = TaskCoordinator(
        repository,
        RuntimeTaskStepRunner(repository, settings, app.state.runtime),
    )
    app.state.task_service = TaskService(
        repository,
        dispatch=app.state.task_coordinator.schedule,
        settings=settings,
    )
    app.include_router(create_task_router(app.state.task_service))

    def missing(resource: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} not found"
        )

    def require_workspace_operator(scope: RequestScope) -> None:
        if scope.embed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Embed sessions cannot access workspace management APIs",
            )

    async def read_plugin_archive(file: UploadFile) -> bytes:
        try:
            archive = await file.read(MAX_ARCHIVE_BYTES + 1)
        finally:
            await file.close()
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Plugin archive exceeds the 10 MiB limit",
            )
        return archive

    def require_run_agent(scope: RequestScope, run: dict) -> None:
        if not scope.agent_id:
            return
        thread = repository.get_thread(scope.workspace_id, run["thread_id"])
        if (
            not thread
            or thread["agent_id"] != scope.agent_id
            or (
                scope.agent_version_id is not None
                and (
                    thread["agent_version_id"] != scope.agent_version_id
                    or run["agent_version_id"] != scope.agent_version_id
                )
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Embed session is bound to another Agent version",
            )

    def require_thread_agent(scope: RequestScope, thread: dict) -> None:
        if scope.agent_id and (
            thread["agent_id"] != scope.agent_id
            or (
                scope.agent_version_id is not None
                and thread["agent_version_id"] != scope.agent_version_id
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Embed session is bound to another Agent version",
            )

    def validate_knowledge_references(
        workspace_id: str,
        definition: AgentDefinition,
    ) -> None:
        missing_sources = [
            source_id
            for source_id in definition.knowledge
            if not repository.get_knowledge_source(workspace_id, source_id)
        ]
        if missing_sources:
            raise HTTPException(
                status_code=422,
                detail=f"Knowledge sources are outside this workspace: {', '.join(missing_sources)}",
            )

    def require_knowledge_service() -> KnowledgeService:
        if not configured_knowledge_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Workspace knowledge indexing is not configured",
            )
        return configured_knowledge_service

    def validate_extension_credential_refs(
        manifest: ExtensionManifest,
        credential_refs: dict[str, str],
    ) -> None:
        allowed = {
            str(requirement.get("id"))
            for requirement in manifest.credential_requirements
            if requirement.get("id")
        }
        unknown = sorted(set(credential_refs) - allowed)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown credential requirements: {', '.join(unknown)}",
            )

    def validate_agent_extension_references(
        workspace_id: str,
        definition: AgentDefinition,
        *,
        require_runnable: bool = False,
    ) -> None:
        installed = {
            extension["manifest_id"]: extension
            for extension in repository.list_extensions(workspace_id)
        }
        missing_extensions = sorted(set(definition.extensions) - set(installed))
        if missing_extensions:
            raise HTTPException(
                status_code=422 if not require_runnable else 409,
                detail="Agent references extensions outside this workspace: "
                + ", ".join(missing_extensions),
            )
        dynamic_tools: dict[str, str] = {}
        for manifest_id, extension in installed.items():
            manifest = ExtensionManifest.model_validate(extension["manifest"])
            entrypoint = next(
                (
                    item
                    for item in manifest.entrypoints
                    if item.type in {"mcp", "openapi", "builtin"}
                ),
                None,
            )
            if entrypoint is None:
                continue
            for tool in manifest.contributions.tools:
                raw_name = str(tool.get("name") or "").strip()
                if raw_name:
                    tool_id = (
                        raw_name
                        if isinstance(entrypoint, BuiltinEntrypoint)
                        else extension_tool_name(manifest_id, raw_name)
                    )
                    dynamic_tools[tool_id] = manifest_id
        unknown_tools = sorted(
            tool
            for tool in definition.tools
            if tool not in BUILTIN_TOOL_IDS and tool not in dynamic_tools
        )
        if unknown_tools:
            raise HTTPException(
                status_code=422 if not require_runnable else 409,
                detail="Agent references unavailable tools: "
                + ", ".join(unknown_tools),
            )
        unbound_tools = sorted(
            tool
            for tool in definition.tools
            if tool in dynamic_tools
            and dynamic_tools[tool] not in definition.extensions
        )
        if unbound_tools:
            raise HTTPException(
                status_code=422 if not require_runnable else 409,
                detail="Bind the contributing extension before using tools: "
                + ", ".join(unbound_tools),
            )
        if require_runnable:
            unavailable = sorted(
                manifest_id
                for manifest_id in definition.extensions
                if installed[manifest_id]["status"] != "enabled"
                or installed[manifest_id]["health"] not in {"healthy", "degraded"}
            )
            if unavailable:
                raise HTTPException(
                    status_code=409,
                    detail="Enable and health-check Agent extensions before publishing: "
                    + ", ".join(unavailable),
                )
            catalog = {
                item["id"]: item
                for item in extension_tool_service.catalog(workspace_id)
            }
            unrunnable_tools = sorted(
                tool
                for tool in definition.tools
                if tool in dynamic_tools and not catalog.get(tool, {}).get("available")
            )
            if unrunnable_tools:
                raise HTTPException(
                    status_code=409,
                    detail="Agent tools are not runnable: " + ", ".join(unrunnable_tools),
                )

    def validate_publishable_definition(
        workspace_id: str,
        definition: AgentDefinition,
    ) -> None:
        validate_knowledge_references(workspace_id, definition)
        validate_agent_extension_references(
            workspace_id,
            definition,
            require_runnable=True,
        )
        if "knowledge.search" in definition.tools and not definition.knowledge:
            raise HTTPException(
                status_code=409,
                detail="Attach at least one knowledge source before publishing knowledge.search",
            )
        if "knowledge.search" in definition.tools:
            require_knowledge_service()
        if "web.search" in definition.tools and not web_search_service:
            raise HTTPException(
                status_code=409,
                detail="Configure web search before publishing web.search",
            )

    def publish_exact_agent_version(
        workspace_id: str,
        agent_id: str,
        version_id: str,
    ) -> dict:
        version = repository.get_agent_version(workspace_id, version_id)
        if not version or version["agent_id"] != agent_id:
            raise missing("Agent version")
        definition = AgentDefinition.model_validate(version["definition"])
        validate_publishable_definition(workspace_id, definition)
        try:
            agent = repository.publish_agent_version(
                workspace_id,
                agent_id,
                version_id,
            )
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not agent:
            raise missing("Agent")
        return agent

    def tool_catalog(workspace_id: str) -> list[dict]:
        return sorted(
            [
                *tool_executor.builtin_catalog(),
                *extension_tool_service.catalog(workspace_id),
            ],
            key=lambda item: (str(item["source"]), str(item["id"])),
        )

    def validate_requested_ui_tool(
        workspace_id: str,
        definition: AgentDefinition,
        requested: RequestedToolCall,
    ) -> None:
        if requested.extension_manifest_id not in definition.extensions:
            raise HTTPException(
                status_code=409, detail="UI action is not bound to this Agent"
            )
        extension = next(
            (
                item
                for item in repository.list_extensions(workspace_id)
                if item["manifest_id"] == requested.extension_manifest_id
            ),
            None,
        )
        if (
            not extension
            or extension["status"] != "enabled"
            or extension["health"] not in {"healthy", "degraded"}
        ):
            raise HTTPException(status_code=409, detail="UI extension is unavailable")
        manifest = ExtensionManifest.model_validate(extension["manifest"])
        block = next(
            (
                item
                for item in manifest.contributions.ui_blocks
                if item.type == "form" and item.id == requested.ui_block_id
            ),
            None,
        )
        if block is None:
            raise HTTPException(status_code=409, detail="UI form is unavailable")
        raw_tool = block.submit.tool
        builtin = any(
            entrypoint.type == "builtin" for entrypoint in manifest.entrypoints
        )
        expected_tool = (
            raw_tool if builtin else extension_tool_name(manifest.id, raw_tool)
        )
        if requested.name != expected_tool or expected_tool not in definition.tools:
            raise HTTPException(status_code=409, detail="UI form tool is unavailable")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "alcuin-api", "version": "0.1.0"}

    @app.get("/v1/bootstrap")
    async def bootstrap(scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        result = repository.bootstrap(scope.workspace_id)
        if not result:
            raise missing("Workspace")
        result["tools"] = tool_catalog(scope.workspace_id)
        return result

    @app.get("/v1/tools")
    async def list_tools(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return tool_catalog(scope.workspace_id)

    @app.get("/v1/providers")
    async def list_providers(scope: ScopeDependency) -> list[dict[str, object]]:
        require_workspace_operator(scope)
        return settings.provider_statuses()

    @app.post(
        "/v1/attachments",
        status_code=status.HTTP_201_CREATED,
        response_model=AttachmentResource,
    )
    async def upload_attachment(
        scope: ScopeDependency,
        file: Annotated[UploadFile, File()],
        upload_id: Annotated[str, Form(min_length=1, max_length=160)],
    ) -> dict:
        require_workspace_operator(scope)
        try:
            content = await file.read(
                max(
                    settings.attachment_image_max_bytes,
                    settings.attachment_document_max_bytes,
                )
                + 1
            )
        finally:
            await file.close()
        try:
            return attachment_service.create(
                scope.workspace_id,
                upload_id=upload_id,
                filename=file.filename or "attachment",
                declared_media_type=file.content_type,
                content=content,
            )
        except AttachmentUploadError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @app.get("/v1/attachments/{attachment_id}", response_model=AttachmentResource)
    async def get_attachment(attachment_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        attachment = repository.get_attachment(scope.workspace_id, attachment_id)
        if attachment is None:
            raise missing("Attachment")
        return public_attachment(attachment)

    @app.get("/v1/attachments/{attachment_id}/content")
    async def get_attachment_content(
        attachment_id: str,
        scope: ScopeDependency,
    ) -> Response:
        require_workspace_operator(scope)
        attachment = repository.get_attachment_blob(
            scope.workspace_id,
            attachment_id,
        )
        if attachment is None:
            raise missing("Attachment")
        disposition = "inline" if attachment["kind"] == "image" else "attachment"
        filename = quote(str(attachment["name"]), safe="")
        return Response(
            content=attachment["content"],
            media_type=str(attachment["media_type"]),
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
                "Cache-Control": "private, no-store",
            },
        )

    @app.delete(
        "/v1/attachments/{attachment_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_attachment(
        attachment_id: str,
        scope: ScopeDependency,
    ) -> Response:
        require_workspace_operator(scope)
        try:
            deleted = repository.delete_attachment(
                scope.workspace_id,
                attachment_id,
            )
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise missing("Attachment")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/skills")
    async def list_skills(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_skills(scope.workspace_id)

    @app.get("/v1/skills/summaries", response_model=list[SkillSummary])
    async def list_skill_summaries(
        scope: ScopeDependency,
        enabled_only: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> list[SkillSummary]:
        """Return bounded list metadata without loading Skill bodies or resources."""
        require_workspace_operator(scope)
        return repository.list_skill_summaries(
            scope.workspace_id,
            enabled_only=enabled_only,
            limit=limit,
            offset=offset,
        )

    @app.post("/v1/skills", status_code=201)
    async def create_skill(payload: SkillCreate, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        try:
            return repository.create_skill(scope.workspace_id, payload)
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Skill contains unsafe or invalid persisted data",
            ) from exc

    @app.get("/v1/skills/{skill_id}")
    async def get_skill(skill_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        skill = repository.get_skill(scope.workspace_id, skill_id)
        if skill is None:
            raise missing("Skill")
        return skill

    @app.patch("/v1/skills/{skill_id}")
    async def update_skill(
        skill_id: str, payload: SkillPatch, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        skill = repository.update_skill(scope.workspace_id, skill_id, payload)
        if skill is None:
            raise missing("Skill")
        return skill

    @app.get("/v1/rules")
    async def list_rules(
        scope: ScopeDependency,
        rule_scope: Annotated[
            Literal["workspace", "thread", "library"] | None,
            Query(alias="scope"),
        ] = None,
        thread_id: str | None = None,
    ) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_rules(
            scope.workspace_id,
            scope=rule_scope,
            thread_id=thread_id,
        )

    @app.get("/v1/rules/summaries", response_model=list[RuleSummary])
    async def list_rule_summaries(
        scope: ScopeDependency,
        rule_scope: Annotated[
            Literal["workspace", "thread", "library"] | None,
            Query(alias="scope"),
        ] = None,
        thread_id: str | None = None,
        enabled_only: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> list[RuleSummary]:
        """Return bounded Rule metadata without loading instruction content."""
        require_workspace_operator(scope)
        return repository.list_rule_summaries(
            scope.workspace_id,
            enabled_only=enabled_only,
            scope=rule_scope,
            thread_id=thread_id,
            limit=limit,
            offset=offset,
        )

    @app.post("/v1/rules", status_code=201)
    async def create_rule(payload: RuleCreate, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        try:
            return repository.create_rule(scope.workspace_id, payload)
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Rule contains unsafe or invalid persisted data",
            ) from exc

    @app.get("/v1/rules/{rule_id}")
    async def get_rule(rule_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        rule = repository.get_rule(scope.workspace_id, rule_id)
        if rule is None:
            raise missing("Rule")
        return rule

    @app.patch("/v1/rules/{rule_id}")
    async def update_rule(
        rule_id: str, payload: RulePatch, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        rule = repository.update_rule(scope.workspace_id, rule_id, payload)
        if rule is None:
            raise missing("Rule")
        return rule

    @app.get("/v1/preferences")
    async def get_workspace_preferences(scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        preferences = repository.get_workspace_preferences(scope.workspace_id)
        if preferences is None:
            raise missing("Workspace")
        return preferences

    @app.patch("/v1/preferences")
    async def update_workspace_preferences(
        payload: PreferenceUpdate, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        try:
            preferences = repository.update_workspace_preferences(
                scope.workspace_id, payload
            )
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Preferences contain unsafe or invalid persisted data",
            ) from exc
        if preferences is None:
            raise missing("Workspace")
        return preferences

    @app.get("/v1/knowledge/sources")
    async def list_knowledge_sources(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_knowledge_sources(scope.workspace_id)

    @app.post("/v1/knowledge/sources", status_code=201)
    async def create_knowledge_source(
        payload: KnowledgeSourceCreate,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        require_knowledge_service()
        try:
            return repository.create_knowledge_source(scope.workspace_id, payload)
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/knowledge/sources/{source_id}/documents")
    async def list_knowledge_documents(
        source_id: str,
        scope: ScopeDependency,
    ) -> list[dict]:
        require_workspace_operator(scope)
        if not repository.get_knowledge_source(scope.workspace_id, source_id):
            raise missing("Knowledge source")
        return repository.list_knowledge_documents(scope.workspace_id, source_id)

    @app.post("/v1/knowledge/sources/{source_id}/documents", status_code=201)
    async def ingest_knowledge_document(
        source_id: str,
        payload: KnowledgeDocumentCreate,
        response: Response,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        service = require_knowledge_service()
        try:
            document, indexed = await service.ingest_document(
                scope.workspace_id,
                source_id,
                payload,
            )
        except KeyError as exc:
            raise missing("Knowledge source") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Knowledge document indexing failed",
            ) from exc
        if not indexed:
            response.status_code = status.HTTP_200_OK
        return {"document": document, "indexed": indexed}

    @app.post("/v1/knowledge/sources/{source_id}/files", status_code=201)
    async def upload_knowledge_file(
        source_id: str,
        scope: ScopeDependency,
        response: Response,
        file: Annotated[UploadFile, File(description="Knowledge document")],
        title: Annotated[str | None, Form(max_length=200)] = None,
    ) -> dict:
        require_workspace_operator(scope)
        service = require_knowledge_service()
        if not repository.get_knowledge_source(scope.workspace_id, source_id):
            raise missing("Knowledge source")
        try:
            data = await file.read(settings.knowledge_upload_max_bytes + 1)
        finally:
            await file.close()
        if len(data) > settings.knowledge_upload_max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Document exceeds the upload size limit",
            )
        try:
            parsed = await asyncio.to_thread(
                configured_document_parser.parse,
                filename=file.filename or "",
                content_type=file.content_type,
                data=data,
                title=title,
            )
        except DocumentTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except UnsupportedDocumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=str(exc),
            ) from exc
        except DocumentParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            document, indexed = await service.ingest_document(
                scope.workspace_id,
                source_id,
                KnowledgeDocumentCreate(
                    title=parsed.title,
                    content=parsed.content,
                    source_uri=parsed.source_uri,
                    metadata=parsed.metadata,
                ),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Knowledge document indexing failed",
            ) from exc
        if not indexed:
            response.status_code = status.HTTP_200_OK
        return {
            "document": document,
            "indexed": indexed,
            "parsed": {
                "filename": parsed.metadata["filename"],
                "kind": parsed.metadata["document_kind"],
                "characters": len(parsed.content),
            },
        }

    @app.get("/v1/knowledge/health")
    async def knowledge_health(scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        return await require_knowledge_service().index.health()

    @app.delete("/v1/knowledge/sources/{source_id}", status_code=204)
    async def delete_knowledge_source(
        source_id: str,
        scope: ScopeDependency,
    ) -> Response:
        require_workspace_operator(scope)
        references = repository.knowledge_source_references(
            scope.workspace_id,
            source_id,
        )
        if references:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Knowledge source is referenced by immutable Agent versions",
                    "references": references,
                },
            )
        try:
            deleted = await require_knowledge_service().delete_source(
                scope.workspace_id,
                source_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Knowledge source deletion failed",
            ) from exc
        if not deleted:
            raise missing("Knowledge source")
        return Response(status_code=204)

    @app.get("/v1/agents")
    async def list_agents(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_agents(scope.workspace_id)

    @app.post("/v1/agents", status_code=201)
    async def create_agent(payload: AgentCreate, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        validate_knowledge_references(scope.workspace_id, payload.definition)
        validate_agent_extension_references(scope.workspace_id, payload.definition)
        try:
            return repository.create_agent(scope.workspace_id, payload)
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/agents/{agent_id}")
    async def get_agent(agent_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        agent = repository.get_agent(scope.workspace_id, agent_id)
        if not agent:
            raise missing("Agent")
        if scope.agent_id and scope.agent_id != agent_id:
            raise HTTPException(
                status_code=403, detail="Embed session is bound to another agent"
            )
        return agent

    @app.post("/v1/agents/{agent_id}/versions", status_code=201)
    async def create_agent_version(
        agent_id: str, payload: AgentVersionCreate, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        validate_knowledge_references(scope.workspace_id, payload.definition)
        validate_agent_extension_references(scope.workspace_id, payload.definition)
        agent = repository.create_agent_version(
            scope.workspace_id, agent_id, payload.definition
        )
        if not agent:
            raise missing("Agent")
        return agent

    @app.get("/v1/agents/{agent_id}/versions")
    async def list_agent_versions(
        agent_id: str,
        scope: ScopeDependency,
    ) -> list[dict]:
        require_workspace_operator(scope)
        if not repository.get_agent(scope.workspace_id, agent_id):
            raise missing("Agent")
        return repository.list_agent_versions(scope.workspace_id, agent_id)

    @app.get("/v1/agents/{agent_id}/versions/{version_id}")
    async def get_agent_version(
        agent_id: str,
        version_id: str,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        version = repository.get_agent_version(scope.workspace_id, version_id)
        if not version or version["agent_id"] != agent_id:
            raise missing("Agent version")
        return version

    @app.post("/v1/agents/{agent_id}/versions/{version_id}/publish")
    async def publish_agent_version(
        agent_id: str,
        version_id: str,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        return publish_exact_agent_version(
            scope.workspace_id,
            agent_id,
            version_id,
        )

    @app.post("/v1/agents/{agent_id}/publish")
    async def publish_agent(agent_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        current = repository.get_agent(scope.workspace_id, agent_id)
        if not current:
            raise missing("Agent")
        version_id = current.get("current_version_id")
        if not version_id:
            raise HTTPException(status_code=409, detail="Agent has no version to publish")
        return publish_exact_agent_version(
            scope.workspace_id,
            agent_id,
            str(version_id),
        )

    @app.get("/v1/threads")
    async def list_threads(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_threads(scope.workspace_id)

    @app.post("/v1/threads", status_code=201)
    async def create_thread(payload: ThreadCreate, scope: ScopeDependency) -> dict:
        scope.require("thread:create")
        if scope.agent_id and scope.agent_id != payload.agent_id:
            raise HTTPException(
                status_code=403, detail="Embed session is bound to another agent"
            )
        agent = repository.get_agent(scope.workspace_id, payload.agent_id)
        if not agent:
            raise missing("Agent")
        if scope.embed:
            if not scope.agent_version_id:
                raise HTTPException(
                    status_code=403,
                    detail="Embed session is not bound to an Agent version",
                )
            if (
                payload.agent_version_id is not None
                and payload.agent_version_id != scope.agent_version_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Embed session is bound to another Agent version",
                )
            selected_version_id = scope.agent_version_id
        else:
            selected_version_id = (
                payload.agent_version_id or agent.get("current_version_id")
            )
        if not selected_version_id:
            raise HTTPException(status_code=409, detail="Agent has no runnable version")
        version = repository.get_agent_version(
            scope.workspace_id,
            str(selected_version_id),
        )
        if not version or version["agent_id"] != payload.agent_id:
            raise HTTPException(
                status_code=422,
                detail="Agent version does not belong to this Agent and Workspace",
            )
        if scope.embed and not version.get("published_at"):
            raise HTTPException(
                status_code=409,
                detail="Embedded Agent version must be published",
            )
        try:
            return repository.create_thread(
                scope.workspace_id,
                payload.agent_id,
                payload.title or "New agent thread",
                payload.context,
                agent_version_id=str(selected_version_id),
            )
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/threads/{thread_id}", response_model=ThreadDetail)
    async def get_thread(thread_id: str, scope: ScopeDependency) -> dict:
        scope.require("run:read")
        thread = repository.get_thread(scope.workspace_id, thread_id)
        if not thread:
            raise missing("Thread")
        require_thread_agent(scope, thread)
        messages = load_thread_messages(repository, scope.workspace_id, thread_id)
        run_ids = list(
            dict.fromkeys(
                str(message["run_id"])
                for message in messages
                if message.get("run_id")
            )
        )
        runs_by_id = {
            str(run["id"]): run
            for run in repository.list_thread_runs(
                scope.workspace_id,
                thread_id,
                limit=500,
            )
        }
        for run_id in run_ids:
            if run_id not in runs_by_id:
                run = repository.get_run(scope.workspace_id, run_id)
                if run is not None:
                    runs_by_id[run_id] = run
        runs = sorted(
            runs_by_id.values(),
            key=lambda run: run["created_at"],
        )
        citation_events = repository.list_thread_citation_events(
            scope.workspace_id, thread_id, run_ids[-50:]
        )
        return {
            "thread": thread,
            "messages": messages,
            "runs": runs,
            "citation_events": citation_events,
        }

    @app.get("/v1/threads/{thread_id}/messages")
    async def list_thread_messages(
        thread_id: str,
        scope: ScopeDependency,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict]:
        scope.require("run:read")
        thread = repository.get_thread(scope.workspace_id, thread_id)
        if not thread:
            raise missing("Thread")
        require_thread_agent(scope, thread)
        return repository.list_messages(
            scope.workspace_id,
            thread_id,
            after=after,
            limit=limit,
        )

    @app.get(
        "/v1/threads/{thread_id}/artifacts",
        response_model=list[ArtifactResource],
    )
    async def list_thread_artifacts(
        thread_id: str,
        scope: ScopeDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict]:
        scope.require("run:read")
        thread = repository.get_thread(scope.workspace_id, thread_id)
        if not thread:
            raise missing("Thread")
        require_thread_agent(scope, thread)
        return repository.list_thread_artifacts(
            scope.workspace_id,
            thread_id,
            limit=limit,
        )

    @app.get("/v1/threads/{thread_id}/configuration")
    async def get_thread_configuration(
        thread_id: str, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        configuration = repository.get_thread_configuration(
            scope.workspace_id, thread_id
        )
        if configuration is None:
            raise missing("Thread")
        return configuration

    @app.patch("/v1/threads/{thread_id}/configuration")
    async def update_thread_configuration(
        thread_id: str,
        payload: ThreadConfigurationUpdate,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        try:
            configuration = repository.update_thread_configuration(
                scope.workspace_id, thread_id, payload
            )
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if configuration is None:
            raise missing("Thread")
        return configuration

    @app.post("/v1/threads/{thread_id}/runs", status_code=202)
    async def create_run(
        thread_id: str, payload: RunCreate, scope: ScopeDependency
    ) -> dict:
        scope.require("run:create")
        thread = repository.get_thread(scope.workspace_id, thread_id)
        if not thread:
            raise missing("Thread")
        require_thread_agent(scope, thread)
        version_id = thread["agent_version_id"]
        version = repository.get_agent_version(scope.workspace_id, version_id)
        if not version or version["agent_id"] != thread["agent_id"]:
            raise HTTPException(
                status_code=409, detail="Thread Agent version is unavailable"
            )
        if scope.embed and not version.get("published_at"):
            raise HTTPException(
                status_code=409,
                detail="Embedded Agent version must be published",
            )
        definition = AgentDefinition.model_validate(version["definition"])
        attachments: tuple[dict, ...] = tuple(
            attachment
            for attachment_id in payload.attachment_ids
            if (
                attachment := repository.get_attachment(
                    scope.workspace_id,
                    attachment_id,
                )
            )
            is not None
        )
        if len(attachments) != len(payload.attachment_ids):
            raise HTTPException(
                status_code=422,
                detail="One or more attachments are unavailable in this Workspace",
            )
        effective_model, reasoning_effort = resolve_run_model_controls(
            settings,
            definition,
            payload,
            attachments,
        )
        if payload.requested_tool:
            validate_requested_ui_tool(
                scope.workspace_id,
                definition,
                payload.requested_tool,
            )
        message_parts = persisted_user_parts(
            payload.input,
            attachments,
            requested_tool_name=(
                payload.requested_tool.name if payload.requested_tool else None
            ),
        )
        attachment_texts = {
            str(attachment["id"]): str(blob["extracted_text"])
            for attachment in attachments
            if attachment.get("kind") == "document"
            and (
                blob := repository.get_attachment_blob(
                    scope.workspace_id,
                    str(attachment["id"]),
                )
            )
            is not None
            and blob.get("extracted_text")
        }
        try:
            run = repository.create_run_with_messages(
                scope.workspace_id,
                thread_id,
                version_id,
                payload.input,
                message_parts,
                app.state.runtime.context_composer.estimate_parts(
                    message_parts,
                    attachment_texts,
                ),
                attachment_ids=tuple(payload.attachment_ids),
                max_total_attachment_bytes=settings.attachment_run_total_max_bytes,
            )
        except AttachmentBindingError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        runtime_attachments = tuple(
            RuntimeAttachmentRef.from_record(
                attachment,
                message_id=str(run.get("input_message_id") or ""),
                current=True,
            )
            for attachment in attachments
        )
        runtime_request = RuntimeRequest(
            workspace_id=scope.workspace_id,
            run_id=run["id"],
            prompt=payload.input,
            thread_context=thread["context"],
            definition=definition,
            current_message_id=run.get("input_message_id"),
            attachments=runtime_attachments,
            thinking=reasoning_effort != ReasoningEffort.NONE,
            effective_model=effective_model,
            reasoning_effort=reasoning_effort,
            requested_tool=(
                payload.requested_tool.model_dump(mode="json")
                if payload.requested_tool
                else None
            ),
            include_workspace_preferences=not scope.embed,
        )
        task = asyncio.create_task(app.state.runtime.execute(runtime_request))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return run

    @app.get("/v1/runs")
    async def list_runs(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_runs(scope.workspace_id)

    @app.get("/v1/runs/{run_id}")
    async def get_run(run_id: str, scope: ScopeDependency) -> dict:
        scope.require("run:read")
        run = repository.get_run(scope.workspace_id, run_id)
        if not run:
            raise missing("Run")
        require_run_agent(scope, run)
        return {**run, "events": repository.list_events(scope.workspace_id, run_id)}

    @app.get("/v1/runs/{run_id}/citations")
    async def get_run_citations(run_id: str, scope: ScopeDependency) -> list[dict]:
        scope.require("run:read")
        run = repository.get_run(scope.workspace_id, run_id)
        if not run:
            raise missing("Run")
        require_run_agent(scope, run)
        return repository.list_run_citation_events(scope.workspace_id, run_id)

    @app.get("/v1/artifacts/{artifact_id}", response_model=ArtifactResource)
    async def get_artifact(artifact_id: str, scope: ScopeDependency) -> dict:
        scope.require("run:read")
        artifact = repository.get_artifact(scope.workspace_id, artifact_id)
        if artifact is None:
            raise missing("Artifact")
        source_run = repository.get_run(
            scope.workspace_id,
            str(artifact["source_run_id"]),
        )
        if source_run is None:
            raise missing("Artifact source Run")
        require_run_agent(scope, source_run)
        return artifact

    @app.get("/v1/artifacts/{artifact_id}/download")
    async def download_artifact(
        artifact_id: str,
        scope: ScopeDependency,
        format: Literal["docx", "html", "md"] = Query(default="docx"),
    ) -> Response:
        artifact = await get_artifact(artifact_id, scope)
        source_run = repository.get_run(scope.workspace_id, str(artifact["source_run_id"]))
        if source_run is None or source_run["status"] not in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Wait for artifact generation to finish before downloading")
        citations = [
            event["payload"] for event in repository.list_events(scope.workspace_id, str(artifact["source_run_id"]))
            if event["type"] == EventType.CITATION_CREATED
        ]
        try:
            content, media_type, extension = await asyncio.to_thread(export_artifact, artifact, format, citations=citations)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = quote(str(artifact["title"]) + "." + extension, safe="")
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename=\"alcuin.{extension}\"; filename*=UTF-8''{filename}",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    @app.patch("/v1/artifacts/{artifact_id}", response_model=ArtifactResource)
    async def update_artifact(
        artifact_id: str,
        payload: ArtifactUpdate,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        try:
            artifact = repository.update_artifact(
                scope.workspace_id,
                artifact_id,
                expected_version=payload.expected_version,
                title=payload.title,
                content=payload.content,
            )
        except ArtifactVersionConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "artifact_version_conflict",
                    "message": str(exc),
                    "current_version": exc.current_version,
                },
            ) from exc
        except RepositoryConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        if artifact is None:
            raise missing("Artifact")
        return artifact

    @app.get("/v1/runs/{run_id}/context")
    async def get_run_context(run_id: str, scope: ScopeDependency) -> dict:
        scope.require("run:read")
        run = repository.get_run(scope.workspace_id, run_id)
        if not run:
            raise missing("Run")
        require_run_agent(scope, run)
        context = repository.get_context_assembly(scope.workspace_id, run_id)
        if not context:
            raise missing("Run context")
        return public_context_assembly(context)

    @app.get("/v1/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        scope: ScopeDependency,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        after: Annotated[int, Query(ge=0)] = 0,
        protocol: Literal["execution", "chat"] = "execution",
    ) -> StreamingResponse:
        scope.require("run:read")
        run = repository.get_run(scope.workspace_id, run_id)
        if not run:
            raise missing("Run")
        require_run_agent(scope, run)
        cursor = resolve_event_cursor(after, last_event_id)

        async def event_stream():
            nonlocal cursor
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    break
                events = repository.list_events(scope.workspace_id, run_id, cursor)
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = event["sequence"]
                        if protocol == "chat":
                            for payload in project_execution_event(event):
                                yield (
                                    f"id: {event['sequence']}\n"
                                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                                )
                        else:
                            yield (
                                f"id: {event['sequence']}\n"
                                f"event: {event['type']}\n"
                                f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            )
                        if event["type"] in {
                            EventType.RUN_COMPLETED,
                            EventType.RUN_FAILED,
                        }:
                            if (
                                protocol == "execution"
                                or event["type"] == EventType.RUN_FAILED
                            ):
                                yield "data: [DONE]\n\n"
                            return
                else:
                    idle_ticks += 1
                    run = repository.get_run(scope.workspace_id, run_id)
                    if run and run["status"] in {
                        "completed",
                        "failed",
                        "cancelled",
                        "waiting_for_approval",
                    }:
                        yield "data: [DONE]\n\n"
                        break
                    if idle_ticks % 20 == 0:
                        yield ": heartbeat\n\n"
                    await asyncio.sleep(0.1)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/runs/{run_id}/approvals/{approval_id}")
    async def decide_approval(
        run_id: str, approval_id: str, payload: ApprovalDecision, scope: ScopeDependency
    ) -> dict:
        scope.require("approval:decide")
        run = repository.get_run(scope.workspace_id, run_id)
        approval = repository.get_approval(scope.workspace_id, approval_id)
        if not run or not approval or approval["run_id"] != run_id:
            raise missing("Approval")
        require_run_agent(scope, run)
        task_link = repository.get_task_run_link(scope.workspace_id, run_id)
        resumed_task = None
        if task_link is not None:
            # Approval and the owning Task cross the governed boundary in one
            # transaction. Concurrent pause/cancel either wins first and prevents
            # execution, or follows the already-authorized in-flight Run.
            try:
                outcome = repository.decide_task_approval(
                    scope.workspace_id,
                    approval_id,
                    payload.decision,
                    payload.note,
                )
            except RepositoryConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            if not outcome:
                raise HTTPException(status_code=409, detail="Approval already decided")
            decided = outcome["approval"]
            resumed_task = outcome["task"]
        else:
            decided = repository.decide_approval(
                scope.workspace_id, approval_id, payload.decision, payload.note
            )
            if not decided:
                raise HTTPException(status_code=409, detail="Approval already decided")

        try:
            await app.state.runtime.resume_after_approval(
                scope.workspace_id,
                run_id,
                payload.decision == "approved",
                approval["request"],
            )
        except Exception:
            if resumed_task is not None:
                # The authorization is durable but the result is not. Never replay
                # a possibly completed mutation automatically; surface an explicit
                # verification boundary instead of leaving the Task stranded.
                current_task = repository.get_task(
                    scope.workspace_id,
                    str(resumed_task["id"]),
                )
                if current_task and current_task["status"] == "running":
                    with suppress(Exception):
                        repository.mark_task_waiting_for_user(
                            scope.workspace_id,
                            str(current_task["id"]),
                            str(current_task["current_step_id"]),
                            expected_revision=int(current_task["revision"]),
                            prompt={
                                "code": "approval_result_verification_required",
                                "message": (
                                    "The approved operation may have executed, but its "
                                    "result was not durably reconciled. Verify the target "
                                    "system before retrying."
                                ),
                                "run_id": run_id,
                                "approval_id": approval_id,
                            },
                        )
            raise

        if resumed_task is not None:
            repository.wake_task_dispatch(
                scope.workspace_id,
                str(resumed_task["id"]),
            )
            app.state.task_coordinator.schedule(
                scope.workspace_id,
                str(resumed_task["id"]),
            )
        return decided

    @app.get("/v1/extensions")
    async def list_extensions(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_extensions(scope.workspace_id)

    @app.post("/v1/plugins/inspect")
    async def inspect_plugin_bundle(
        scope: ScopeDependency,
        file: Annotated[UploadFile, File(description="Agent or Cursor Plugin ZIP")],
    ) -> dict:
        """Inspect an inert archive; no component is installed or executed."""
        require_workspace_operator(scope)
        archive = await read_plugin_archive(file)
        try:
            inspection = await asyncio.to_thread(inspect_plugin_archive, archive)
        except PluginArchiveError as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc
        issued = issue_plugin_inspection_receipt(
            signing_secret=settings.signing_secret,
            workspace_id=scope.workspace_id,
            archive=archive,
            permissions=inspection.permissions,
            policy_revision=PLUGIN_INSPECTION_POLICY_REVISION,
        )
        return {
            "inspection_receipt": issued.receipt,
            "inspection_digest": issued.inspection_digest,
            "permissions_hash": issued.permissions_hash,
            "policy_revision": issued.policy_revision,
            "expires_at": issued.expires_at,
            "inspection": inspection.public_dict(),
        }

    @app.post("/v1/plugins/install", status_code=201)
    async def install_plugin_bundle(
        scope: ScopeDependency,
        file: Annotated[UploadFile, File(description="Previously inspected Plugin ZIP")],
        inspection_receipt: Annotated[
            str,
            Form(min_length=32, max_length=4096),
        ],
    ) -> dict:
        """Re-inspect the exact bytes and install portable resources disabled-first."""
        require_workspace_operator(scope)
        archive = await read_plugin_archive(file)
        try:
            inspection = await asyncio.to_thread(inspect_plugin_archive, archive)
            verify_plugin_inspection_receipt(
                inspection_receipt,
                signing_secret=settings.signing_secret,
                workspace_id=scope.workspace_id,
                archive=archive,
                permissions=inspection.permissions,
                policy_revision=PLUGIN_INSPECTION_POLICY_REVISION,
            )
            draft = customization_bundle_draft(inspection)
            installed = repository.install_customization_bundle(
                scope.workspace_id,
                skills=list(draft.skills),
                rules=list(draft.rules),
            )
        except PluginArchiveError as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc
        except PluginInspectionReceiptError as exc:
            failure_status = (
                status.HTTP_410_GONE
                if exc.code == "plugin_inspection_receipt_expired"
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(
                status_code=failure_status,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc
        return {
            **installed,
            "plugin": {
                "format": inspection.format,
                "name": inspection.name,
                "version": inspection.version,
            },
            "status": "installed_disabled",
            "mcp_servers": list(inspection.mcp_servers),
            "mcp_install_state": (
                "needs_connection_review" if inspection.mcp_servers else "not_declared"
            ),
            "disabled_components": list(inspection.disabled_components),
            "warnings": list(inspection.warnings),
        }

    @app.post("/v1/extensions/inspect")
    async def inspect_extension(
        payload: ManifestInspectRequest, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        return inspect_manifest(payload.manifest)

    @app.post("/v1/extensions/import/openapi")
    async def import_openapi(
        payload: OpenAPIImportRequest, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        try:
            resolved = await resolve_openapi_document(
                payload,
                allow_private_networks=settings.extension_allow_private_networks,
            )
            manifest = manifest_from_openapi(resolved)
            entrypoint = next(
                (item for item in manifest.entrypoints if item.type == "openapi"),
                None,
            )
            if (
                entrypoint
                and entrypoint.base_url
                and not settings.extension_allow_private_networks
                and not await is_public_http_url(str(entrypoint.base_url))
            ):
                raise ValueError(
                    "OpenAPI base_url must resolve to a public HTTP endpoint"
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:300]) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=422,
                detail="OpenAPI specification could not be fetched",
            ) from exc
        return inspect_manifest(manifest)

    @app.post("/v1/extensions/import/mcp")
    async def import_mcp(payload: MCPImportRequest, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        if (
            payload.entrypoint.transport != "stdio"
            and payload.entrypoint.url
            and not settings.extension_allow_private_networks
            and not await is_public_http_url(str(payload.entrypoint.url))
        ):
            raise HTTPException(
                status_code=422,
                detail="Remote MCP URL must resolve to a public HTTP endpoint",
            )
        try:
            tools = await configured_mcp_gateway.discover(payload.entrypoint)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="MCP connection or tool discovery failed",
            ) from exc
        return inspect_manifest(manifest_from_mcp(payload, tools))

    @app.post("/v1/extensions", status_code=201)
    async def install_extension(
        payload: ExtensionInstallRequest, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        validate_extension_credential_refs(payload.manifest, payload.credential_refs)
        return repository.install_extension(
            scope.workspace_id, payload.manifest, payload.credential_refs
        )

    @app.patch("/v1/extensions/{extension_id}/credentials")
    async def bind_extension_credentials(
        extension_id: str,
        payload: ExtensionCredentialBinding,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        manifest = ExtensionManifest.model_validate(extension["manifest"])
        validate_extension_credential_refs(manifest, payload.credential_refs)
        updated = repository.update_extension_credentials(
            scope.workspace_id,
            extension_id,
            payload.credential_refs,
        )
        return updated or {}

    @app.post("/v1/extensions/{extension_id}/health")
    async def extension_health(extension_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        report = await check_extension_health(
            extension,
            mcp_gateway=configured_mcp_gateway,
            openapi_gateway=configured_openapi_gateway,
        )
        if report.status == "healthy" and isinstance(report.details.get("tools"), list):
            manifest = ExtensionManifest.model_validate(extension["manifest"])
            if any(entrypoint.type == "mcp" for entrypoint in manifest.entrypoints):
                repository.update_extension_manifest(
                    scope.workspace_id,
                    extension_id,
                    refresh_mcp_manifest(manifest, report.details["tools"]),
                )
        repository.update_extension(
            scope.workspace_id, extension_id, health=report.status
        )
        return report.model_dump(mode="json")

    @app.patch("/v1/extensions/{extension_id}")
    async def set_extension_status(
        extension_id: str, payload: ExtensionStatusUpdate, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        if payload.enabled:
            missing_credentials = missing_credential_ids(extension)
            if missing_credentials:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Bind required credentials before enabling: "
                        + ", ".join(missing_credentials)
                    ),
                )
        if payload.enabled and extension["health"] not in {"healthy", "degraded"}:
            raise HTTPException(
                status_code=409, detail="Run a health check before enabling"
            )
        updated = repository.update_extension(
            scope.workspace_id,
            extension_id,
            status="enabled" if payload.enabled else "disabled",
        )
        return updated or {}

    @app.post("/v1/extensions/{extension_id}/tools/refresh")
    async def refresh_extension_tools(
        extension_id: str, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        entrypoint = next(
            (
                item
                for item in extension["manifest"].get("entrypoints", [])
                if item.get("type") == "mcp"
            ),
            None,
        )
        if not entrypoint:
            raise HTTPException(
                status_code=409, detail="Extension has no MCP entrypoint"
            )
        try:
            tools = await configured_mcp_gateway.discover(
                MCPEntrypoint.model_validate(entrypoint)
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP connection or tool discovery failed",
            ) from exc
        manifest = ExtensionManifest.model_validate(extension["manifest"])
        repository.update_extension_manifest(
            scope.workspace_id,
            extension_id,
            refresh_mcp_manifest(manifest, tools),
        )
        repository.update_extension(scope.workspace_id, extension_id, health="healthy")
        return {"tools": tools, "count": len(tools)}

    @app.post("/v1/extensions/{extension_id}/tools/{tool_name}:call")
    async def call_extension_tool(
        extension_id: str,
        tool_name: str,
        payload: MCPToolCallRequest,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        if extension["status"] != "enabled":
            raise HTTPException(status_code=409, detail="Extension is disabled")
        entrypoint = next(
            (
                item
                for item in extension["manifest"].get("entrypoints", [])
                if item.get("type") == "mcp"
            ),
            None,
        )
        if not entrypoint:
            raise HTTPException(
                status_code=409, detail="Extension has no MCP entrypoint"
            )
        declared_tool = next(
            (
                tool
                for tool in extension["manifest"]
                .get("contributions", {})
                .get("tools", [])
                if tool.get("name") == tool_name
            ),
            None,
        )
        if declared_tool is None:
            raise missing("MCP tool")
        if declared_tool and declared_tool.get("mutating"):
            raise HTTPException(
                status_code=409,
                detail="Mutating tools must be called through an approval-gated Agent run",
            )
        try:
            return await configured_mcp_gateway.call(
                MCPEntrypoint.model_validate(entrypoint), tool_name, payload.arguments
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP tool execution failed",
            ) from exc

    @app.post("/v1/extensions/{extension_id}/openapi/{tool_name}:call")
    async def call_openapi_tool(
        extension_id: str,
        tool_name: str,
        payload: MCPToolCallRequest,
        scope: ScopeDependency,
    ) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        if extension["status"] != "enabled":
            raise HTTPException(status_code=409, detail="Extension is disabled")
        entrypoint = next(
            (
                item
                for item in extension["manifest"].get("entrypoints", [])
                if item.get("type") == "openapi"
            ),
            None,
        )
        tool = next(
            (
                item
                for item in extension["manifest"]
                .get("contributions", {})
                .get("tools", [])
                if item.get("name") == tool_name
            ),
            None,
        )
        if not entrypoint or not tool:
            raise missing("OpenAPI tool")
        if tool.get("mutating"):
            raise HTTPException(
                status_code=409,
                detail="Mutating tools must be called through an approval-gated Agent run",
            )
        credential_reference = extension["credential_refs"].get("api-credential")
        try:
            return await configured_openapi_gateway.call(
                OpenAPIEntrypoint.model_validate(entrypoint),
                tool,
                payload.arguments,
                credential_reference,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:300]) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="OpenAPI tool execution failed",
            ) from exc

    @app.post("/v1/embed/sessions", status_code=201)
    async def create_embed_session(
        payload: EmbedSessionCreate, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        agent = repository.get_agent(scope.workspace_id, payload.agent_id)
        if not agent:
            raise missing("Agent")
        if agent["status"] != "published":
            raise HTTPException(
                status_code=409, detail="Publish the agent before embedding"
            )
        published_version_id = agent.get("published_version_id")
        if not published_version_id:
            raise HTTPException(
                status_code=409,
                detail="Agent has no published version to embed",
            )
        published_version = repository.get_agent_version(
            scope.workspace_id,
            str(published_version_id),
        )
        if (
            not published_version
            or published_version["agent_id"] != payload.agent_id
            or not published_version.get("published_at")
        ):
            raise HTTPException(
                status_code=409,
                detail="Agent published version is unavailable",
            )
        now = int(time.time())
        claims = EmbedClaims(
            workspace_id=scope.workspace_id,
            agent_id=payload.agent_id,
            agent_version_id=str(published_version_id),
            origin=payload.origin,
            allowed_actions=payload.allowed_actions,
            issued_at=now,
            expires_at=now + payload.ttl_seconds,
        )
        return {
            "token": issue_embed_token(claims),
            "expires_at": claims.expires_at,
            "agent_version_id": claims.agent_version_id,
            "origin": claims.origin,
        }

    return app


app = create_app()
