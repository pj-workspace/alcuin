from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from alcuin_core.tools import ToolCitation, ToolContext, ToolError, ToolResult
from jsonschema import Draft202012Validator

__all__ = [
    "ToolCitation",
    "ToolContext",
    "ToolDefinition",
    "ToolError",
    "ToolExecution",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "provider_tool_name",
]


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]
DynamicToolResolver = Callable[[str], Iterable["ToolDefinition"]]


_INVALID_PROVIDER_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def provider_tool_name(name: str) -> str:
    """Map namespaced Alcuin tool ids to OpenAI-compatible function names."""
    return _INVALID_PROVIDER_NAME.sub("_", name)[:64]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    mutating: bool = False
    timeout_seconds: float = 15.0
    max_calls_per_run: int = 4
    available: bool = True

    @property
    def provider_name(self) -> str:
        return provider_tool_name(self.name)

    def provider_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.provider_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class ToolExecution:
    definition: ToolDefinition
    result: ToolResult
    duration_ms: int


class ToolRegistry:
    """Process-local registry for built-in and adapted extension tools."""

    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._provider_names: dict[str, str] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Tool is already registered: {definition.name}")
        if not definition.provider_name:
            raise ValueError(
                "Tool name must contain at least one provider-safe character"
            )
        conflict = self._provider_names.get(definition.provider_name)
        if conflict:
            raise ValueError(
                f"Tool provider-name collision: {definition.name} and {conflict}"
            )
        Draft202012Validator.check_schema(definition.input_schema)
        self._definitions[definition.name] = definition
        self._provider_names[definition.provider_name] = definition.name

    def resolve(self, names: Iterable[str]) -> list[ToolDefinition]:
        return [self._definitions[name] for name in names if name in self._definitions]

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def values(self) -> tuple[ToolDefinition, ...]:
        """Return the registered definitions without exposing the mutable registry."""
        return tuple(self._definitions.values())

    def canonical_name(self, provider_name: str, allowed_names: Iterable[str]) -> str:
        canonical = self._provider_names.get(provider_name, provider_name)
        return canonical if canonical in set(allowed_names) else provider_name


class ToolExecutor:
    """Workspace-scoped execution boundary shared by all runtime adapters."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        dynamic_resolver: DynamicToolResolver | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.dynamic_resolver = dynamic_resolver

    def _dynamic_registry(self, workspace_id: str) -> ToolRegistry:
        definitions = (
            self.dynamic_resolver(workspace_id) if self.dynamic_resolver else ()
        )
        return ToolRegistry(definitions)

    def builtin_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "id": definition.name,
                "name": definition.name,
                "description": definition.description,
                "source": "builtin",
                "extension_manifest_id": None,
                "extension_name": None,
                "mutating": definition.mutating,
                "available": True,
                "status": "available",
            }
            for definition in self.registry.values()
        ]

    def provider_schemas(
        self,
        allowed_names: Iterable[str],
        *,
        workspace_id: str = "",
    ) -> list[dict[str, Any]]:
        return [
            definition.provider_schema()
            for definition in self.definitions(allowed_names, workspace_id=workspace_id)
        ]

    def definitions(
        self,
        allowed_names: Iterable[str],
        *,
        workspace_id: str = "",
    ) -> list[ToolDefinition]:
        dynamic = self._dynamic_registry(workspace_id)
        resolved: list[ToolDefinition] = []
        provider_names: dict[str, str] = {}
        for name in dict.fromkeys(allowed_names):
            definition = self.registry.get(name) or dynamic.get(name)
            if definition is None or not definition.available:
                continue
            conflict = provider_names.get(definition.provider_name)
            if conflict and conflict != definition.name:
                raise ToolError(
                    "tool_name_conflict",
                    f"Tool names conflict after provider normalization: {conflict}, {definition.name}",
                )
            provider_names[definition.provider_name] = definition.name
            resolved.append(definition)
        return resolved

    def definition(
        self,
        name: str,
        allowed_names: Iterable[str],
        *,
        workspace_id: str = "",
    ) -> ToolDefinition:
        allowed = set(allowed_names)
        definition = self.registry.get(name) or self._dynamic_registry(
            workspace_id
        ).get(name)
        if name not in allowed or definition is None:
            raise ToolError(
                "tool_not_allowed", f"Tool is not available to this agent: {name}"
            )
        return definition

    def canonical_name(
        self,
        provider_name: str,
        allowed_names: Iterable[str],
        *,
        workspace_id: str = "",
    ) -> str:
        matches = [
            definition.name
            for definition in self.definitions(allowed_names, workspace_id=workspace_id)
            if definition.provider_name == provider_name
        ]
        if len(matches) > 1:
            raise ToolError("tool_name_conflict", "Provider tool name is ambiguous")
        return matches[0] if matches else provider_name

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allowed_names: Iterable[str],
        context: ToolContext,
    ) -> ToolExecution:
        definition = self.definition(
            name,
            allowed_names,
            workspace_id=context.workspace_id,
        )
        errors = sorted(
            Draft202012Validator(definition.input_schema).iter_errors(arguments),
            key=lambda error: list(error.path),
        )
        if errors:
            path = ".".join(str(part) for part in errors[0].path)
            prefix = f"{path}: " if path else ""
            raise ToolError("invalid_arguments", f"{prefix}{errors[0].message}")

        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                definition.handler(context, arguments),
                timeout=max(0.1, definition.timeout_seconds),
            )
        except TimeoutError as exc:
            raise ToolError(
                "tool_timeout",
                f"Tool exceeded its {definition.timeout_seconds:g}s deadline",
            ) from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("tool_failed", "Tool execution failed") from exc

        if not isinstance(result, ToolResult):
            raise ToolError(
                "invalid_result", "Tool returned an invalid result envelope"
            )
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return ToolExecution(
            definition=definition, result=result, duration_ms=duration_ms
        )
