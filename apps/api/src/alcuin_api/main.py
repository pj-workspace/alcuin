from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import Settings, get_settings
from .contracts import (
    AgentCreate,
    AgentDefinition,
    AgentVersionCreate,
    ApprovalDecision,
    EmbedClaims,
    EmbedSessionCreate,
    EventType,
    ExtensionInstallRequest,
    ExtensionStatusUpdate,
    KnowledgeDocumentCreate,
    KnowledgeSourceCreate,
    ManifestInspectRequest,
    MCPEntrypoint,
    MCPToolCallRequest,
    OpenAPIImportRequest,
    OpenAPIEntrypoint,
    RunCreate,
    ThreadCreate,
)
from .document_parser import (
    DocumentParseError,
    DocumentParser,
    DocumentTooLargeError,
    FileDocumentParser,
    UnsupportedDocumentError,
)
from .extensions import (
    check_extension_health,
    inspect_manifest,
    manifest_from_openapi,
    resolve_openapi_document,
)
from .mcp_gateway import MCPGateway
from .knowledge import KnowledgeService, QdrantKnowledgeIndex
from .openapi_gateway import OpenAPIGateway
from .runtime import RuntimeOrchestrator, RuntimeRequest
from .security import RequestScope, issue_embed_token, resolve_scope
from .store import Store
from .tcm_sse import project_execution_event
from .tools import ToolExecutor, ToolRegistry
from .web_search import WebSearchService


ScopeDependency = Annotated[RequestScope, Depends(resolve_scope)]


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    knowledge_service: KnowledgeService | None = None,
    document_parser: DocumentParser | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    repository = store or Store(settings.database_path)
    web_search_service = (
        WebSearchService(settings) if (settings.searxng_url or "").strip() else None
    )
    configured_knowledge_service = knowledge_service or (
        KnowledgeService(repository, QdrantKnowledgeIndex(settings))
        if (settings.qdrant_url or "").strip()
        and (settings.dashscope_api_key or "").strip()
        else None
    )
    configured_document_parser = document_parser or FileDocumentParser(settings)
    definitions = []
    if web_search_service:
        definitions.append(web_search_service.tool_definition())
    if configured_knowledge_service:
        definitions.append(configured_knowledge_service.tool_definition())
    tool_registry = ToolRegistry(definitions)
    tool_executor = ToolExecutor(tool_registry)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.tasks = set()
        yield
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
    app.state.web_search_service = web_search_service
    app.state.knowledge_service = configured_knowledge_service
    app.state.runtime = RuntimeOrchestrator(repository, settings, tool_executor)

    def missing(resource: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} not found")

    def require_workspace_operator(scope: RequestScope) -> None:
        if scope.embed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Embed sessions cannot access workspace management APIs",
            )

    def require_run_agent(scope: RequestScope, run: dict) -> None:
        if not scope.agent_id:
            return
        thread = repository.get_thread(scope.workspace_id, run["thread_id"])
        if not thread or thread["agent_id"] != scope.agent_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Embed session is bound to another agent",
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

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "alcuin-api", "version": "0.1.0"}

    @app.get("/v1/bootstrap")
    async def bootstrap(scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        result = repository.bootstrap(scope.workspace_id)
        if not result:
            raise missing("Workspace")
        return result

    @app.get("/v1/providers")
    async def list_providers(scope: ScopeDependency) -> list[dict[str, object]]:
        require_workspace_operator(scope)
        return settings.provider_statuses()

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
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="A knowledge source with this name already exists",
            ) from exc

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
        return repository.create_agent(scope.workspace_id, payload)

    @app.get("/v1/agents/{agent_id}")
    async def get_agent(agent_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        agent = repository.get_agent(scope.workspace_id, agent_id)
        if not agent:
            raise missing("Agent")
        if scope.agent_id and scope.agent_id != agent_id:
            raise HTTPException(status_code=403, detail="Embed session is bound to another agent")
        return agent

    @app.post("/v1/agents/{agent_id}/versions", status_code=201)
    async def create_agent_version(agent_id: str, payload: AgentVersionCreate, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        validate_knowledge_references(scope.workspace_id, payload.definition)
        agent = repository.create_agent_version(scope.workspace_id, agent_id, payload.definition)
        if not agent:
            raise missing("Agent")
        return agent

    @app.post("/v1/agents/{agent_id}/publish")
    async def publish_agent(agent_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        current = repository.get_agent(scope.workspace_id, agent_id)
        if not current:
            raise missing("Agent")
        definition = AgentDefinition.model_validate(current["definition"])
        validate_knowledge_references(scope.workspace_id, definition)
        if "knowledge.search" in definition.tools and not definition.knowledge:
            raise HTTPException(
                status_code=409,
                detail="Attach at least one knowledge source before publishing knowledge.search",
            )
        if "knowledge.search" in definition.tools:
            require_knowledge_service()
        agent = repository.publish_agent(scope.workspace_id, agent_id)
        if not agent:
            raise missing("Agent")
        return agent

    @app.get("/v1/threads")
    async def list_threads(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_threads(scope.workspace_id)

    @app.post("/v1/threads", status_code=201)
    async def create_thread(payload: ThreadCreate, scope: ScopeDependency) -> dict:
        scope.require("thread:create")
        if scope.agent_id and scope.agent_id != payload.agent_id:
            raise HTTPException(status_code=403, detail="Embed session is bound to another agent")
        agent = repository.get_agent(scope.workspace_id, payload.agent_id)
        if not agent:
            raise missing("Agent")
        if scope.embed and agent["status"] != "published":
            raise HTTPException(status_code=409, detail="Embedded agents must be published")
        return repository.create_thread(
            scope.workspace_id,
            payload.agent_id,
            payload.title or "New agent thread",
            payload.context,
        )

    @app.post("/v1/threads/{thread_id}/runs", status_code=202)
    async def create_run(thread_id: str, payload: RunCreate, scope: ScopeDependency) -> dict:
        scope.require("run:create")
        thread = repository.get_thread(scope.workspace_id, thread_id)
        if not thread:
            raise missing("Thread")
        if scope.agent_id and scope.agent_id != thread["agent_id"]:
            raise HTTPException(status_code=403, detail="Embed session is bound to another agent")
        agent = repository.get_agent(scope.workspace_id, thread["agent_id"])
        if not agent or not agent.get("current_version_id"):
            raise HTTPException(status_code=409, detail="Agent has no runnable version")
        version_id = scope.agent_version_id or agent["current_version_id"]
        version = repository.get_agent_version(scope.workspace_id, version_id)
        if not version or version["agent_id"] != thread["agent_id"]:
            raise HTTPException(status_code=403, detail="Agent version is outside the embed scope")
        run = repository.create_run(
            scope.workspace_id, thread_id, version_id, payload.input
        )
        runtime_request = RuntimeRequest(
            workspace_id=scope.workspace_id,
            run_id=run["id"],
            prompt=payload.input,
            thread_context=thread["context"],
            definition=AgentDefinition.model_validate(version["definition"]),
            attachments=tuple(payload.attachments),
            thinking=payload.thinking,
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

    @app.get("/v1/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        scope: ScopeDependency,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        after: int = 0,
        protocol: Literal["execution", "tcm"] = "execution",
    ) -> StreamingResponse:
        scope.require("run:read")
        run = repository.get_run(scope.workspace_id, run_id)
        if not run:
            raise missing("Run")
        require_run_agent(scope, run)
        cursor = max(after, int(last_event_id or 0))

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
                        if protocol == "tcm":
                            for payload in project_execution_event(event):
                                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
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
                            if protocol == "execution" or event["type"] == EventType.RUN_FAILED:
                                yield "data: [DONE]\n\n"
                            return
                else:
                    idle_ticks += 1
                    run = repository.get_run(scope.workspace_id, run_id)
                    if run and run["status"] in {"completed", "failed", "cancelled", "waiting_for_approval"}:
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
        decided = repository.decide_approval(
            scope.workspace_id, approval_id, payload.decision, payload.note
        )
        if not decided:
            raise HTTPException(status_code=409, detail="Approval already decided")
        app.state.runtime.resume_after_approval(
            scope.workspace_id,
            run_id,
            payload.decision == "approved",
            approval["request"],
        )
        return decided

    @app.get("/v1/extensions")
    async def list_extensions(scope: ScopeDependency) -> list[dict]:
        require_workspace_operator(scope)
        return repository.list_extensions(scope.workspace_id)

    @app.post("/v1/extensions/inspect")
    async def inspect_extension(payload: ManifestInspectRequest, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        return inspect_manifest(payload.manifest)

    @app.post("/v1/extensions/import/openapi")
    async def import_openapi(payload: OpenAPIImportRequest, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        try:
            resolved = await resolve_openapi_document(payload)
            manifest = manifest_from_openapi(resolved)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:300]) from exc
        return inspect_manifest(manifest)

    @app.post("/v1/extensions", status_code=201)
    async def install_extension(payload: ExtensionInstallRequest, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        return repository.install_extension(
            scope.workspace_id, payload.manifest, payload.credential_refs
        )

    @app.post("/v1/extensions/{extension_id}/health")
    async def extension_health(extension_id: str, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        report = await check_extension_health(extension)
        repository.update_extension(scope.workspace_id, extension_id, health=report.status)
        return report.model_dump(mode="json")

    @app.patch("/v1/extensions/{extension_id}")
    async def set_extension_status(
        extension_id: str, payload: ExtensionStatusUpdate, scope: ScopeDependency
    ) -> dict:
        require_workspace_operator(scope)
        extension = repository.get_extension(scope.workspace_id, extension_id)
        if not extension:
            raise missing("Extension")
        if payload.enabled and extension["health"] not in {"healthy", "degraded"}:
            raise HTTPException(status_code=409, detail="Run a health check before enabling")
        updated = repository.update_extension(
            scope.workspace_id, extension_id, status="enabled" if payload.enabled else "disabled"
        )
        return updated or {}

    @app.post("/v1/extensions/{extension_id}/tools/refresh")
    async def refresh_extension_tools(extension_id: str, scope: ScopeDependency) -> dict:
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
            raise HTTPException(status_code=409, detail="Extension has no MCP entrypoint")
        tools = await MCPGateway().discover(MCPEntrypoint.model_validate(entrypoint))
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
            raise HTTPException(status_code=409, detail="Extension has no MCP entrypoint")
        declared_tool = next(
            (
                tool
                for tool in extension["manifest"].get("contributions", {}).get("tools", [])
                if tool.get("name") == tool_name
            ),
            None,
        )
        if declared_tool and declared_tool.get("mutating"):
            raise HTTPException(
                status_code=409,
                detail="Mutating tools must be called through an approval-gated Agent run",
            )
        return await MCPGateway().call(
            MCPEntrypoint.model_validate(entrypoint), tool_name, payload.arguments
        )

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
                for item in extension["manifest"].get("contributions", {}).get("tools", [])
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
            return await OpenAPIGateway().call(
                OpenAPIEntrypoint.model_validate(entrypoint),
                tool,
                payload.arguments,
                credential_reference,
            )
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc

    @app.post("/v1/embed/sessions", status_code=201)
    async def create_embed_session(payload: EmbedSessionCreate, scope: ScopeDependency) -> dict:
        require_workspace_operator(scope)
        agent = repository.get_agent(scope.workspace_id, payload.agent_id)
        if not agent:
            raise missing("Agent")
        if agent["status"] != "published":
            raise HTTPException(status_code=409, detail="Publish the agent before embedding")
        now = int(time.time())
        claims = EmbedClaims(
            workspace_id=scope.workspace_id,
            agent_id=payload.agent_id,
            agent_version_id=agent["current_version_id"],
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
