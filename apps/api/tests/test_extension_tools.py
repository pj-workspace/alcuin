from __future__ import annotations

from typing import Any

import pytest

from alcuin_api.config import Settings
from alcuin_core.contracts import AgentDefinition, ExtensionManifest
from alcuin_api.extension_tools import (
    MAX_EXTENSION_EVENT_RESULT_CHARS,
    ExtensionToolService,
    bounded_result,
    extension_tool_name,
)
from alcuin_api.store import Store
from alcuin_api.runtime import RuntimeOrchestrator
from alcuin_api.tools import ToolContext, ToolError, ToolExecutor
from alcuin_operations_copilot import seed_operations_demo


class RecordingMCPGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, _entrypoint, tool_name: str, arguments: dict[str, Any]) -> dict:
        self.calls.append((tool_name, arguments))
        return {
            "is_error": False,
            "content": [],
            "structured_content": {"echoed": arguments["message"]},
        }


class RecordingOpenAPIGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None, bool]] = []

    async def call(
        self,
        _entrypoint,
        tool: dict[str, Any],
        arguments: dict[str, Any],
        credential_reference: str | None,
        *,
        allow_mutating: bool = False,
    ) -> dict:
        self.calls.append(
            (str(tool["name"]), arguments, credential_reference, allow_mutating)
        )
        return {"status_code": 200, "result": {"id": arguments["id"]}}


async def recording_builtin_adapter(
    _context: ToolContext,
    tool_name: str,
    arguments: dict[str, Any],
):
    from alcuin_api.tools import ToolResult

    return ToolResult(
        data={"tool": tool_name, "value": arguments["value"]},
        summary="Built-in adapter completed",
    )


def install_enabled(store: Store, manifest: ExtensionManifest, refs: dict[str, str] | None = None) -> dict:
    extension = store.install_extension("ws_demo", manifest, refs or {})
    store.update_extension("ws_demo", extension["id"], status="enabled", health="healthy")
    return store.get_extension("ws_demo", extension["id"]) or {}


def test_extension_event_results_are_bounded() -> None:
    result = bounded_result({"content": "x" * (MAX_EXTENSION_EVENT_RESULT_CHARS + 100)})
    assert result["truncated"] is True
    assert result["original_characters"] > MAX_EXTENSION_EVENT_RESULT_CHARS
    assert len(result["content"]) == MAX_EXTENSION_EVENT_RESULT_CHARS


def test_runtime_redacts_resolved_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALCUIN_SECRET_WORKSPACE_RUNTIME_TEST", "runtime-secret-value")
    orchestrator = RuntimeOrchestrator(
        Store(":memory:"),
        Settings(
            database_path=":memory:",
            openai_api_key="",
            deepseek_api_key="",
            dashscope_api_key="",
            qdrant_api_key="",
        ),
    )
    assert orchestrator.redact_payload(
        {"ordinary_field": "prefix runtime-secret-value suffix"}
    ) == {"ordinary_field": "prefix [REDACTED] suffix"}


@pytest.mark.asyncio
async def test_builtin_extension_uses_explicit_registered_adapter() -> None:
    store = Store(":memory:")
    service = ExtensionToolService(
        store,
        RecordingMCPGateway(),
        RecordingOpenAPIGateway(),
        {"verification-adapter": recording_builtin_adapter},
    )
    manifest = ExtensionManifest.model_validate(
        {
            "id": "verification.builtin",
            "name": "Built-in Verification",
            "version": "0.1.0",
            "contributions": {
                "tools": [
                    {
                        "name": "verification.echo",
                        "description": "Echo through a trusted adapter",
                        "input_schema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    }
                ]
            },
            "entrypoints": [
                {"type": "builtin", "adapter": "verification-adapter"}
            ],
        }
    )
    install_enabled(store, manifest)
    executor = ToolExecutor(dynamic_resolver=service.definitions)

    execution = await executor.execute(
        "verification.echo",
        {"value": "registered"},
        allowed_names=["verification.echo"],
        context=ToolContext("ws_demo", "run_builtin", {}),
    )

    assert execution.result.data == {
        "tool": "verification.echo",
        "value": "registered",
    }


@pytest.mark.asyncio
async def test_dynamic_mcp_tool_is_workspace_scoped_and_rechecks_extension_state() -> None:
    store = Store(":memory:")
    mcp = RecordingMCPGateway()
    service = ExtensionToolService(store, mcp, RecordingOpenAPIGateway())
    manifest = ExtensionManifest.model_validate(
        {
            "id": "verification.dynamic-mcp",
            "name": "Dynamic MCP",
            "version": "0.1.0",
            "contributions": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo a message",
                        "input_schema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                            "additionalProperties": False,
                        },
                        "mutating": False,
                    }
                ]
            },
            "entrypoints": [
                {"type": "mcp", "transport": "stdio", "command": "test-mcp"}
            ],
        }
    )
    extension = install_enabled(store, manifest)
    tool_name = extension_tool_name(manifest.id, "echo")
    executor = ToolExecutor(dynamic_resolver=service.definitions)
    context = ToolContext("ws_demo", "run_dynamic", {})

    execution = await executor.execute(
        tool_name,
        {"message": "hello"},
        allowed_names=[tool_name],
        context=context,
    )
    assert execution.result.data == {"echoed": "hello"}
    assert mcp.calls == [("echo", {"message": "hello"})]

    with pytest.raises(ToolError) as outside_workspace:
        await executor.execute(
            tool_name,
            {"message": "hello"},
            allowed_names=[tool_name],
            context=ToolContext("ws_other", "run_other", {}),
        )
    assert outside_workspace.value.code == "tool_not_allowed"

    definition = service.definitions("ws_demo")[0]
    store.update_extension("ws_demo", extension["id"], status="disabled")
    with pytest.raises(ToolError) as disabled:
        await definition.handler(context, {"message": "blocked"})
    assert disabled.value.code == "extension_unavailable"


@pytest.mark.asyncio
async def test_dynamic_openapi_tool_uses_server_side_credential_reference() -> None:
    store = Store(":memory:")
    openapi = RecordingOpenAPIGateway()
    service = ExtensionToolService(store, RecordingMCPGateway(), openapi)
    manifest = ExtensionManifest.model_validate(
        {
            "id": "verification.dynamic-openapi",
            "name": "Dynamic OpenAPI",
            "version": "0.1.0",
            "contributions": {
                "tools": [
                    {
                        "name": "getRecord",
                        "description": "Get a record",
                        "method": "GET",
                        "path": "/records/{id}",
                        "input_schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                        "mutating": False,
                    }
                ]
            },
            "entrypoints": [
                {
                    "type": "openapi",
                    "base_url": "https://api.example.com",
                    "auth": "bearer",
                }
            ],
            "credential_requirements": [
                {"id": "api-credential", "type": "bearer", "required": True}
            ],
        }
    )
    install_enabled(
        store,
        manifest,
        {"api-credential": "secret://workspace/records"},
    )
    tool_name = extension_tool_name(manifest.id, "getRecord")
    executor = ToolExecutor(dynamic_resolver=service.definitions)

    execution = await executor.execute(
        tool_name,
        {"id": "R-42"},
        allowed_names=[tool_name],
        context=ToolContext("ws_demo", "run_openapi", {}),
    )
    assert execution.result.data == {"status_code": 200, "result": {"id": "R-42"}}
    assert openapi.calls == [
        ("getRecord", {"id": "R-42"}, "secret://workspace/records", False)
    ]


@pytest.mark.asyncio
async def test_approved_mutating_extension_tool_executes_real_handler() -> None:
    store = Store(":memory:")
    openapi = RecordingOpenAPIGateway()
    service = ExtensionToolService(store, RecordingMCPGateway(), openapi)
    manifest = ExtensionManifest.model_validate(
        {
            "id": "verification.write-openapi",
            "name": "Write OpenAPI",
            "version": "0.1.0",
            "contributions": {
                "tools": [
                    {
                        "name": "updateRecord",
                        "description": "Update a record",
                        "method": "PATCH",
                        "path": "/records/{id}",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "status": {"type": "string"},
                            },
                            "required": ["id", "status"],
                        },
                        "mutating": True,
                    }
                ]
            },
            "entrypoints": [
                {
                    "type": "openapi",
                    "base_url": "https://api.example.com",
                    "auth": "bearer",
                }
            ],
            "credential_requirements": [
                {"id": "api-credential", "type": "bearer", "required": True}
            ],
            "permissions": [
                {
                    "id": "records:write",
                    "reason": "Update records through OpenAPI",
                    "risk": "high",
                }
            ],
        }
    )
    install_enabled(
        store,
        manifest,
        {"api-credential": "secret://workspace/write"},
    )
    tool_name = extension_tool_name(manifest.id, "updateRecord")
    executor = ToolExecutor(dynamic_resolver=service.definitions)
    context = ToolContext("ws_demo", "run_unapproved", {})
    with pytest.raises(ToolError) as blocked:
        await executor.execute(
            tool_name,
            {"id": "R-9", "status": "closed"},
            allowed_names=[tool_name],
            context=context,
        )
    assert blocked.value.code == "approval_required"
    assert openapi.calls == []

    seed_operations_demo(store)
    agent = store.get_agent("ws_demo", "agt_operations")
    assert agent is not None
    definition = AgentDefinition.model_validate(agent["definition"])
    definition = definition.model_copy(
        update={
            "extensions": [*definition.extensions, manifest.id],
            "tools": [*definition.tools, tool_name],
        }
    )
    updated_agent = store.create_agent_version(
        "ws_demo",
        "agt_operations",
        definition,
    )
    assert updated_agent and updated_agent["current_version_id"]
    thread = store.create_thread("ws_demo", "agt_operations", "Approval test", {})
    run = store.create_run(
        "ws_demo",
        thread["id"],
        updated_agent["current_version_id"],
        "Update R-9",
    )
    store.set_run_status("ws_demo", run["id"], "waiting_for_approval")
    orchestrator = RuntimeOrchestrator(
        store,
        Settings(
            database_path=":memory:",
            openai_api_key="",
            deepseek_api_key="",
            dashscope_api_key="",
            qdrant_api_key="",
        ),
        executor,
    )
    await orchestrator.resume_after_approval(
        "ws_demo",
        run["id"],
        True,
        {
            "tool": tool_name,
            "call_id": "call_write_1",
            "arguments": {"id": "R-9", "status": "closed"},
        },
    )

    assert openapi.calls == [
        (
            "updateRecord",
            {"id": "R-9", "status": "closed"},
            "secret://workspace/write",
            True,
        )
    ]
    completed = store.get_run("ws_demo", run["id"])
    assert completed and completed["status"] == "completed"
    events = store.list_events("ws_demo", run["id"])
    tool_result = next(event for event in events if event["type"] == "tool.completed")
    assert tool_result["payload"]["status"] == "succeeded"
    assert tool_result["payload"]["result"] == {
        "status_code": 200,
        "result": {"id": "R-9"},
    }
    assert events[-1]["type"] == "run.completed"
