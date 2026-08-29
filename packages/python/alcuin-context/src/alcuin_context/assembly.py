"""Deterministic, provider-neutral context assembly.

The assembler deliberately receives already-authorized sources. Rules, Skills, host context, and
messages are resources owned by higher-level services; this package only establishes ordering,
budgeting, and traceability. Provider adapters serialize the returned envelope without rebuilding
the prompt themselves.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol


class ContextLayer(StrEnum):
    PLATFORM = "platform"
    WORKSPACE_RULES = "workspace_rules"
    USER_PREFERENCES = "user_preferences"
    AGENT_INSTRUCTIONS = "agent_instructions"
    THREAD_RULES = "thread_rules"
    ACTIVE_SKILLS = "active_skills"
    COMPACTED_CONVERSATION = "compacted_conversation"
    HOST_CONTEXT = "host_context"


COMPOSABLE_CONTEXT_LAYERS: tuple[ContextLayer, ...] = (
    ContextLayer.PLATFORM,
    ContextLayer.WORKSPACE_RULES,
    ContextLayer.USER_PREFERENCES,
    ContextLayer.AGENT_INSTRUCTIONS,
    ContextLayer.THREAD_RULES,
    ContextLayer.ACTIVE_SKILLS,
    ContextLayer.COMPACTED_CONVERSATION,
    ContextLayer.HOST_CONTEXT,
)
_LAYER_ORDER = {layer: index for index, layer in enumerate(COMPOSABLE_CONTEXT_LAYERS)}


class TokenEstimator(Protocol):
    """Estimate model input size without coupling the kernel to one tokenizer."""

    revision: str

    def estimate_text(self, text: str) -> int: ...


@dataclass(frozen=True)
class HeuristicTokenEstimator:
    """Conservative UTF-8 estimate used until a model-specific tokenizer is registered.

    Three UTF-8 bytes per token avoids severely undercounting CJK text while remaining simple and
    deterministic. Provider integrations may replace this through the `TokenEstimator` protocol.
    """

    bytes_per_token: float = 3.0
    revision: str = "utf8-bytes-v1"

    def __post_init__(self) -> None:
        if self.bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be positive")

    def estimate_text(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text.encode("utf-8")) / self.bytes_per_token))


@dataclass(frozen=True)
class TokenBudget:
    """Input budget after reserving output and provider/tool protocol overhead."""

    context_window_tokens: int
    reserved_output_tokens: int = 4_096
    reserved_tool_tokens: int = 0
    compaction_trigger_ratio: float = 0.8

    def __post_init__(self) -> None:
        if self.context_window_tokens < 1:
            raise ValueError("context_window_tokens must be positive")
        if self.reserved_output_tokens < 0 or self.reserved_tool_tokens < 0:
            raise ValueError("reserved token counts cannot be negative")
        if not 0 < self.compaction_trigger_ratio <= 1:
            raise ValueError("compaction_trigger_ratio must be in (0, 1]")
        if self.available_input_tokens < 1:
            raise ValueError("reserved tokens leave no input capacity")

    @property
    def available_input_tokens(self) -> int:
        return (
            self.context_window_tokens
            - self.reserved_output_tokens
            - self.reserved_tool_tokens
        )

    @property
    def compaction_trigger_tokens(self) -> int:
        return max(1, math.floor(self.available_input_tokens * self.compaction_trigger_ratio))


@dataclass(frozen=True)
class ContextSection:
    id: str
    layer: ContextLayer
    content: str
    source_id: str | None = None
    source_version: str | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("context section id cannot be blank")
        if not self.content.strip():
            raise ValueError("context section content cannot be blank")


@dataclass(frozen=True)
class ContextMessage:
    id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    run_id: str | None = None
    estimated_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("context message id cannot be blank")
        if self.sequence < 1:
            raise ValueError("context message sequence must be positive")
        if not self.content.strip():
            raise ValueError("context message content cannot be blank")
        if self.estimated_tokens is not None and self.estimated_tokens < 0:
            raise ValueError("estimated_tokens cannot be negative")


@dataclass(frozen=True)
class CompactionRecord:
    id: str
    through_sequence: int
    summary: str
    source_message_ids: tuple[str, ...]
    source_digest: str
    strategy: str
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if self.through_sequence < 1:
            raise ValueError("compaction through_sequence must be positive")
        if not self.summary.strip():
            raise ValueError("compaction summary cannot be blank")
        if not self.source_message_ids:
            raise ValueError("compaction must cite at least one source message")
        if not self.source_digest.strip():
            raise ValueError("compaction source_digest cannot be blank")


@dataclass(frozen=True)
class ContextTraceEntry:
    kind: Literal["section", "message", "compaction"]
    id: str
    layer: str
    estimated_tokens: int
    digest: str
    source_id: str | None = None
    source_version: str | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class ContextAssemblyRequest:
    sections: tuple[ContextSection, ...]
    messages: tuple[ContextMessage, ...]
    budget: TokenBudget
    current_message_id: str
    compaction: CompactionRecord | None = None


@dataclass(frozen=True)
class ContextAssembly:
    system_prompt: str
    messages: tuple[ContextMessage, ...]
    trace: tuple[ContextTraceEntry, ...]
    estimated_tokens: int
    token_budget: int
    compaction_trigger_tokens: int
    estimator_revision: str
    compaction_id: str | None = None
    compaction_required: bool = False

    def normalized_input(self) -> dict[str, object]:
        """Return the stable envelope persisted for a Run and serialized by adapters."""
        return {
            "system_prompt": self.system_prompt,
            "messages": [
                {
                    "id": message.id,
                    "sequence": message.sequence,
                    "role": message.role,
                    "content": message.content,
                    "run_id": message.run_id,
                }
                for message in self.messages
            ],
            "estimated_tokens": self.estimated_tokens,
            "token_budget": self.token_budget,
            "compaction_id": self.compaction_id,
            "estimator_revision": self.estimator_revision,
        }


class ContextBudgetExceeded(ValueError):
    def __init__(self, *, estimated_tokens: int, token_budget: int) -> None:
        self.estimated_tokens = estimated_tokens
        self.token_budget = token_budget
        super().__init__(
            f"assembled context requires about {estimated_tokens} tokens; budget is {token_budget}"
        )


class ContextCompactor(Protocol):
    """Summarize a complete immutable message prefix without invoking Agent tools."""

    async def compact(
        self,
        *,
        messages: tuple[ContextMessage, ...],
        parent: CompactionRecord | None,
        target_tokens: int,
    ) -> CompactionRecord: ...


@dataclass(frozen=True)
class ExtractiveContextCompactor:
    """Deterministic fallback compactor over complete user/assistant turns.

    This implementation never calls a provider or Agent tool. It keeps the beginning and newest
    evidence when the extract exceeds the target, while the record still binds the exact immutable
    source prefix. A model-backed compactor can replace it through `ContextCompactor`.
    """

    estimator: TokenEstimator = field(default_factory=HeuristicTokenEstimator)
    revision: str = "extractive-turns-v1"

    async def compact(
        self,
        *,
        messages: tuple[ContextMessage, ...],
        parent: CompactionRecord | None,
        target_tokens: int,
    ) -> CompactionRecord:
        if target_tokens < 1:
            raise ValueError("target_tokens must be positive")
        ordered = tuple(sorted(messages, key=lambda message: message.sequence))
        if len({message.sequence for message in ordered}) != len(ordered):
            raise ValueError("compaction message sequences must be unique")

        complete_through = 0
        awaiting_assistant = False
        for index, message in enumerate(ordered):
            if not awaiting_assistant:
                if message.role != "user":
                    break
                awaiting_assistant = True
                continue
            if message.role != "assistant":
                break
            awaiting_assistant = False
            complete_through = index + 1
        if complete_through == 0:
            raise ValueError("compaction requires at least one complete conversation turn")

        sources = ordered[:complete_through]
        if parent:
            parent_sources = tuple(
                message
                for message in sources
                if message.sequence <= parent.through_sequence
            )
            if tuple(message.id for message in parent_sources) != parent.source_message_ids:
                raise ValueError("parent compaction is not the exact source prefix")
            if compaction_source_digest(parent_sources) != parent.source_digest:
                raise ValueError("parent compaction source digest does not match messages")

        new_sources = tuple(
            message
            for message in sources
            if parent is None or message.sequence > parent.through_sequence
        )
        fragments = ["Conversation summary (deterministic extract):"]
        if parent:
            fragments.append(f"Prior summary: {self._normalize(parent.summary)}")
        fragments.extend(
            f"{message.role.title()}: {self._normalize(message.content)}"
            for message in new_sources
        )
        summary = self._fit("\n".join(fragments), target_tokens)
        source_digest = compaction_source_digest(sources)
        return CompactionRecord(
            id=f"cmp_{source_digest[:20]}",
            through_sequence=sources[-1].sequence,
            summary=summary,
            source_message_ids=tuple(message.id for message in sources),
            source_digest=source_digest,
            strategy=self.revision,
            parent_id=parent.id if parent else None,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.split())

    def _fit(self, text: str, target_tokens: int) -> str:
        if self.estimator.estimate_text(text) <= target_tokens:
            return text
        marker = "\n… earlier detail omitted; immutable sources retained …\n"
        if self.estimator.estimate_text(marker) >= target_tokens:
            raise ValueError("target_tokens is too small for a traceable compaction")

        low, high = 1, max(1, len(text) - len(marker))
        best: str | None = None
        while low <= high:
            kept = (low + high) // 2
            head = max(1, math.ceil(kept * 0.55))
            tail = max(1, kept - head)
            candidate = text[:head].rstrip() + marker + text[-tail:].lstrip()
            if self.estimator.estimate_text(candidate) <= target_tokens:
                best = candidate
                low = kept + 1
            else:
                high = kept - 1
        if best is None:
            raise ValueError("target_tokens is too small for a traceable compaction")
        return best


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compaction_source_digest(messages: tuple[ContextMessage, ...]) -> str:
    """Hash exact source ids, order, roles, and content for later provenance checks."""
    return _digest(
        [
            {
                "id": message.id,
                "sequence": message.sequence,
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]
    )


@dataclass
class ContextAssembler:
    estimator: TokenEstimator = field(default_factory=HeuristicTokenEstimator)

    def assemble(self, request: ContextAssemblyRequest) -> ContextAssembly:
        ordered_sections = sorted(
            enumerate(request.sections),
            key=lambda item: (_LAYER_ORDER[item[1].layer], item[0]),
        )
        messages = tuple(sorted(request.messages, key=lambda message: message.sequence))
        if len({message.sequence for message in messages}) != len(messages):
            raise ValueError("context message sequences must be unique")
        current = next(
            (message for message in messages if message.id == request.current_message_id),
            None,
        )
        if current is None:
            raise ValueError("current message is missing from the assembled conversation")
        if current.role != "user" or current.sequence != messages[-1].sequence:
            raise ValueError("current message must be the latest user message")

        compaction = request.compaction
        if compaction:
            source_ids = set(compaction.source_message_ids)
            sources = tuple(message for message in messages if message.id in source_ids)
            if len(sources) != len(source_ids):
                raise ValueError("compaction references messages outside this assembly")
            if compaction_source_digest(sources) != compaction.source_digest:
                raise ValueError("compaction source digest does not match immutable messages")
            messages = tuple(
                message
                for message in messages
                if message.sequence > compaction.through_sequence
            )
            if not any(message.id == request.current_message_id for message in messages):
                raise ValueError("compaction cannot cover the current user message")

        rendered_sections: list[str] = []
        trace: list[ContextTraceEntry] = []
        total_tokens = 0
        for _, section in ordered_sections:
            title = section.title or section.layer.value.replace("_", " ").title()
            rendered = f"<alcuin_context layer=\"{section.layer.value}\" title=\"{title}\">\n{section.content.strip()}\n</alcuin_context>"
            rendered_sections.append(rendered)
            tokens = self.estimator.estimate_text(rendered)
            total_tokens += tokens
            trace.append(
                ContextTraceEntry(
                    kind="section",
                    id=section.id,
                    layer=section.layer.value,
                    estimated_tokens=tokens,
                    digest=_digest(section.content),
                    source_id=section.source_id,
                    source_version=section.source_version,
                )
            )

        if compaction:
            rendered = (
                "<alcuin_context layer=\"compacted_conversation\" title=\"Conversation Summary\">\n"
                f"{compaction.summary.strip()}\n"
                "</alcuin_context>"
            )
            rendered_sections.append(rendered)
            tokens = self.estimator.estimate_text(rendered)
            total_tokens += tokens
            trace.append(
                ContextTraceEntry(
                    kind="compaction",
                    id=compaction.id,
                    layer=ContextLayer.COMPACTED_CONVERSATION.value,
                    estimated_tokens=tokens,
                    digest=_digest(compaction.summary),
                    source_id=compaction.source_digest,
                    sequence=compaction.through_sequence,
                )
            )

        for message in messages:
            tokens = (
                message.estimated_tokens
                if message.estimated_tokens is not None
                else self.estimator.estimate_text(message.content)
            )
            # Include a small, deterministic role/message envelope allowance.
            tokens += 4
            total_tokens += tokens
            trace.append(
                ContextTraceEntry(
                    kind="message",
                    id=message.id,
                    layer="conversation",
                    estimated_tokens=tokens,
                    digest=_digest(message.content),
                    source_id=message.run_id,
                    sequence=message.sequence,
                )
            )

        if total_tokens > request.budget.available_input_tokens:
            raise ContextBudgetExceeded(
                estimated_tokens=total_tokens,
                token_budget=request.budget.available_input_tokens,
            )

        return ContextAssembly(
            system_prompt="\n\n".join(rendered_sections),
            messages=messages,
            trace=tuple(trace),
            estimated_tokens=total_tokens,
            token_budget=request.budget.available_input_tokens,
            compaction_trigger_tokens=request.budget.compaction_trigger_tokens,
            estimator_revision=self.estimator.revision,
            compaction_id=compaction.id if compaction else None,
            compaction_required=(
                total_tokens > request.budget.compaction_trigger_tokens
                and any(message.role == "assistant" for message in messages[:-1])
            ),
        )
