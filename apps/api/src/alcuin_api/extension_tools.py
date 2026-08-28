from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from alcuin_core.contracts import (
    BuiltinEntrypoint,
    ExtensionManifest,
    MCPEntrypoint,
    OpenAPIEntrypoint,
)
from .mcp_gateway import MCPGateway
from .openapi_gateway import OpenAPIGateway
from .store import Store
from .tools import ToolContext, ToolDefinition, ToolError, ToolResult


MAX_EXTENSION_EVENT_RESULT_CHARS = 64_000
BuiltinToolAdapter = Callable[[ToolContext, str, dict[str, Any]], Awaitable[ToolResult]]


def extension_tool_name(manifest_id: str, tool_name: str) -> str:
    """Return the portable Agent Definition id for an extension contribution."""
    return f"extension.{manifest_id}.{tool_name}"


def bounded_result(data: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), default=str
    )
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
        builtin_adapters: Mapping[str, BuiltinToolAdapter] | None = None,
    ) -> None:
        self.store = store
        self.mcp_gateway = mcp_gateway
        self.openapi_gateway = openapi_gateway
        self.builtin_adapters = dict(builtin_adapters or {})

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
                    if item.type in {"mcp", "openapi", "builtin"}
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
                        name=(
                            raw_name
                            if isinstance(entrypoint, BuiltinEntrypoint)
                            else extension_tool_name(manifest.id, raw_name)
                        ),
                        description=str(tool.get("description") or f"Call {raw_name}")[
                            :500
                        ],
                        input_schema=input_schema,
                        handler=self._handler(extension["id"], manifest.id, raw_name),
                        mutating=bool(tool.get("mutating")),
                        timeout_seconds=self._bounded_number(
                            tool.get("timeout_seconds"),
                            default=15.0,
                            minimum=1.0,
                            maximum=30.0,
                        ),
                        max_calls_per_run=int(
                            self._bounded_number(
                                tool.get("max_calls_per_run"),
                                default=4,
                                minimum=1,
                                maximum=4,
                            )
                        ),
                        available=(
                            not isinstance(entrypoint, BuiltinEntrypoint)
                            or entrypoint.adapter in self.builtin_adapters
                        ),
                    )
                )
        return definitions

    def catalog(self, workspace_id: str) -> list[dict[str, Any]]:
        """Describe installed contributions, including tools that are not runnable yet."""
        catalog: list[dict[str, Any]] = []
        for extension in self.store.list_extensions(workspace_id):
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
            available = extension["status"] == "enabled" and extension["health"] in {
                "healthy",
                "degraded",
            }
            status = "available"
            if extension["status"] != "enabled":
                status = "disabled"
            elif extension["health"] == "unchecked":
                status = "unchecked"
            elif extension["health"] not in {"healthy", "degraded"}:
                status = "unhealthy"
            elif (
                isinstance(entrypoint, BuiltinEntrypoint)
                and entrypoint.adapter not in self.builtin_adapters
            ):
                available = False
                status = "adapter_missing"
            for tool in manifest.contributions.tools:
                raw_name = str(tool.get("name") or "").strip()
                if not raw_name:
                    continue
                catalog.append(
                    {
                        "id": (
                            raw_name
                            if isinstance(entrypoint, BuiltinEntrypoint)
                            else extension_tool_name(manifest.id, raw_name)
                        ),
                        "name": raw_name,
                        "description": str(
                            tool.get("description") or f"Call {raw_name}"
                        )[:500],
                        "source": "extension",
                        "extension_manifest_id": manifest.id,
                        "extension_name": manifest.name,
                        "mutating": bool(tool.get("mutating")),
                        "available": available,
                        "status": status,
                    }
                )
        return catalog

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
        async def execute(
            context: ToolContext, arguments: dict[str, Any]
        ) -> ToolResult:
            extension = self.store.get_extension(context.workspace_id, extension_id)
            if not extension or extension.get("manifest_id") != manifest_id:
                raise ToolError(
                    "extension_unavailable",
                    "Extension is not available in this workspace",
                )
            if extension["status"] != "enabled" or extension["health"] not in {
                "healthy",
                "degraded",
            }:
                raise ToolError(
                    "extension_unavailable", "Extension is disabled or unhealthy"
                )
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
                raise ToolError(
                    "tool_not_allowed", "Extension tool is no longer approved"
                )
            if tool.get("mutating") and not context.mutation_authorized:
                raise ToolError(
                    "approval_required",
                    "Mutating extension tools require an authorized Agent execution",
                )
            citations = ()
            entrypoint = next(
                (
                    item
                    for item in manifest.entrypoints
                    if item.type in {"mcp", "openapi", "builtin"}
                ),
                None,
            )
            if isinstance(entrypoint, MCPEntrypoint):
                result = await self.mcp_gateway.call(entrypoint, raw_name, arguments)
                if result.get("is_error"):
                    raise ToolError(
                        "extension_tool_failed", "MCP tool reported a failure"
                    )
                structured = result.get("structured_content")
                data = (
                    structured if isinstance(structured, dict) else {"result": result}
                )
            elif isinstance(entrypoint, OpenAPIEntrypoint):
                result = await self.openapi_gateway.call(
                    entrypoint,
                    tool,
                    arguments,
                    extension.get("credential_refs", {}).get("api-credential"),
                    allow_mutating=context.mutation_authorized,
                )
                data = result if isinstance(result, dict) else {"result": result}
            elif isinstance(entrypoint, BuiltinEntrypoint):
                adapter = self.builtin_adapters.get(entrypoint.adapter)
                if adapter is None:
                    raise ToolError(
                        "extension_unavailable",
                        "Built-in extension adapter is not registered",
                    )
                adapter_result = await adapter(context, raw_name, arguments)
                data = adapter_result.data
                citations = adapter_result.citations
            else:
                raise ToolError(
                    "extension_unavailable", "Extension has no executable entrypoint"
                )
            return ToolResult(
                data=bounded_result(data),
                summary=f"{manifest.name} completed {raw_name}",
                citations=citations,
            )

        return execute
