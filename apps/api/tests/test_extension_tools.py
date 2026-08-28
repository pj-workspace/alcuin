from __future__ import annotations

from typing import Any

import pytest

from alcuin_api.contracts import ExtensionManifest
from alcuin_api.extension_tools import (
    MAX_EXTENSION_EVENT_RESULT_CHARS,
    ExtensionToolService,
    bounded_result,
    extension_tool_name,
)
from alcuin_api.store import Store
from alcuin_api.tools import ToolContext, ToolError, ToolExecutor


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
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def call(
        self,
        _entrypoint,
        tool: dict[str, Any],
        arguments: dict[str, Any],
        credential_reference: str | None,
    ) -> dict:
        self.calls.append((str(tool["name"]), arguments, credential_reference))
        return {"status_code": 200, "result": {"id": arguments["id"]}}


def install_enabled(store: Store, manifest: ExtensionManifest, refs: dict[str, str] | None = None) -> dict:
    extension = store.install_extension("ws_demo", manifest, refs or {})
    store.update_extension("ws_demo", extension["id"], status="enabled", health="healthy")
    return store.get_extension("ws_demo", extension["id"]) or {}


def test_extension_event_results_are_bounded() -> None:
    result = bounded_result({"content": "x" * (MAX_EXTENSION_EVENT_RESULT_CHARS + 100)})
    assert result["truncated"] is True
    assert result["original_characters"] > MAX_EXTENSION_EVENT_RESULT_CHARS
    assert len(result["content"]) == MAX_EXTENSION_EVENT_RESULT_CHARS


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
        ("getRecord", {"id": "R-42"}, "secret://workspace/records")
    ]
