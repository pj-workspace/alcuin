from __future__ import annotations

import json
from typing import Any

from .contracts import ExtensionManifest, MCPEntrypoint, OpenAPIEntrypoint
from .mcp_gateway import MCPGateway
from .openapi_gateway import OpenAPIGateway
from .store import Store
from .tools import ToolContext, ToolDefinition, ToolError, ToolResult


MAX_EXTENSION_EVENT_RESULT_CHARS = 64_000


def extension_tool_name(manifest_id: str, tool_name: str) -> str:
    """Return the portable Agent Definition id for an extension contribution."""
    return f"extension.{manifest_id}.{tool_name}"


def bounded_result(data: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(serialized) <= MAX_EXTENSION_EVENT_RESULT_CHARS:
        return data
    return {
        "truncated": True,
        "original_characters": len(serialized),
        "content": serialized[:MAX_EXTENSION_EVENT_RESULT_CHARS],
    }


class ExtensionToolService:
    """Resolve enabled extension contributions at the workspace execution boundary."""

    def __init__(
        self,
        store: Store,
        mcp_gateway: MCPGateway,
        openapi_gateway: OpenAPIGateway,
    ) -> None:
        self.store = store
        self.mcp_gateway = mcp_gateway
        self.openapi_gateway = openapi_gateway

    def definitions(self, workspace_id: str) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for extension in self.store.list_extensions(workspace_id):
            if extension["status"] != "enabled" or extension["health"] not in {
                "healthy",
                "degraded",
            }:
                continue
            manifest = ExtensionManifest.model_validate(extension["manifest"])
            entrypoint = next(
                (
                    item
                    for item in manifest.entrypoints
                    if item.type in {"mcp", "openapi"}
                ),
                None,
            )
            if entrypoint is None:
                continue
            for tool in manifest.contributions.tools:
                raw_name = str(tool.get("name") or "").strip()
                input_schema = tool.get("input_schema")
                if not raw_name or not isinstance(input_schema, dict):
                    continue
                definitions.append(
                    ToolDefinition(
                        name=extension_tool_name(manifest.id, raw_name),
                        description=str(tool.get("description") or f"Call {raw_name}")[:500],
                        input_schema=input_schema,
                        handler=self._handler(extension["id"], manifest.id, raw_name),
                        mutating=bool(tool.get("mutating")),
                        timeout_seconds=self._bounded_number(
                            tool.get("timeout_seconds"), default=15.0, minimum=1.0, maximum=30.0
                        ),
                        max_calls_per_run=int(
                            self._bounded_number(
                                tool.get("max_calls_per_run"),
                                default=4,
                                minimum=1,
                                maximum=4,
                            )
                        ),
                    )
                )
        return definitions

    @staticmethod
    def _bounded_number(
        value: Any,
        *,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        return min(maximum, max(minimum, number))

    def _handler(self, extension_id: str, manifest_id: str, raw_name: str):
        async def execute(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
            extension = self.store.get_extension(context.workspace_id, extension_id)
            if not extension or extension.get("manifest_id") != manifest_id:
                raise ToolError("extension_unavailable", "Extension is not available in this workspace")
            if extension["status"] != "enabled" or extension["health"] not in {
                "healthy",
                "degraded",
            }:
                raise ToolError("extension_unavailable", "Extension is disabled or unhealthy")
            manifest = ExtensionManifest.model_validate(extension["manifest"])
            tool = next(
                (
                    item
                    for item in manifest.contributions.tools
                    if item.get("name") == raw_name
                ),
                None,
            )
            if tool is None:
                raise ToolError("tool_not_allowed", "Extension tool is no longer approved")
            if tool.get("mutating") and not context.mutation_authorized:
                raise ToolError(
                    "approval_required",
                    "Mutating extension tools require an authorized Agent execution",
                )
            entrypoint = next(
                (
                    item
                    for item in manifest.entrypoints
                    if item.type in {"mcp", "openapi"}
                ),
                None,
            )
            if isinstance(entrypoint, MCPEntrypoint):
                result = await self.mcp_gateway.call(entrypoint, raw_name, arguments)
                if result.get("is_error"):
                    raise ToolError("extension_tool_failed", "MCP tool reported a failure")
                structured = result.get("structured_content")
                data = structured if isinstance(structured, dict) else {"result": result}
            elif isinstance(entrypoint, OpenAPIEntrypoint):
                result = await self.openapi_gateway.call(
                    entrypoint,
                    tool,
                    arguments,
                    extension.get("credential_refs", {}).get("api-credential"),
                    allow_mutating=context.mutation_authorized,
                )
                data = result if isinstance(result, dict) else {"result": result}
            else:
                raise ToolError("extension_unavailable", "Extension has no executable entrypoint")
            return ToolResult(
                data=bounded_result(data),
                summary=f"{manifest.name} completed {raw_name}",
            )

        return execute
