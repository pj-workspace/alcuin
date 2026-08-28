"""Stable tool envelopes shared by runtimes, connectors, and Extensions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


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
    mutation_authorized: bool = False


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
