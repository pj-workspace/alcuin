from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator


class ToolError(Exception):
    """A controlled tool failure safe to expose to the model and run trace."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolContext:
    workspace_id: str
    run_id: str
    thread_context: dict[str, Any]
    knowledge_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCitation:
    label: str
    source: str
    locator: str
    snippet: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_event_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "source": self.source,
            "locator": self.locator,
        }
        if self.snippet:
            payload["snippet"] = self.snippet
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True)
class ToolResult:
    data: dict[str, Any]
    summary: str
    citations: tuple[ToolCitation, ...] = ()

    def model_content(self, *, max_chars: int = 24_000) -> str:
        content = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        if len(content) <= max_chars:
            return content
        envelope = {
            "truncated": True,
            "summary": self.summary,
            "content": content[:max_chars],
        }
        return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]


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
            raise ValueError("Tool name must contain at least one provider-safe character")
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

    def canonical_name(self, provider_name: str, allowed_names: Iterable[str]) -> str:
        canonical = self._provider_names.get(provider_name, provider_name)
        return canonical if canonical in set(allowed_names) else provider_name


class ToolExecutor:
    """Workspace-scoped execution boundary shared by all runtime adapters."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()

    def provider_schemas(self, allowed_names: Iterable[str]) -> list[dict[str, Any]]:
        return [definition.provider_schema() for definition in self.registry.resolve(allowed_names)]

    def definitions(self, allowed_names: Iterable[str]) -> list[ToolDefinition]:
        return self.registry.resolve(allowed_names)

    def definition(self, name: str, allowed_names: Iterable[str]) -> ToolDefinition:
        allowed = set(allowed_names)
        definition = self.registry.get(name)
        if name not in allowed or definition is None:
            raise ToolError("tool_not_allowed", f"Tool is not available to this agent: {name}")
        return definition

    def canonical_name(self, provider_name: str, allowed_names: Iterable[str]) -> str:
        return self.registry.canonical_name(provider_name, allowed_names)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allowed_names: Iterable[str],
        context: ToolContext,
    ) -> ToolExecution:
        definition = self.definition(name, allowed_names)
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
            raise ToolError("invalid_result", "Tool returned an invalid result envelope")
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return ToolExecution(definition=definition, result=result, duration_ms=duration_ms)
