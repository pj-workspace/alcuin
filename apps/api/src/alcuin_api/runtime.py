from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from .config import ProviderConfig, Settings
from .contracts import AgentDefinition, EventType, ImageAttachment
from .security import redact_sensitive, redact_text
from .store import Store


RUNTIME_PRESENTATION_PROTOCOL = """\
<alcuin_runtime_presentation>
- Match the user's language in both the final answer and any provider-visible reasoning.
- Treat provider-visible reasoning as polished progress copy shown in the Studio, not as a raw private scratchpad.
- Keep it concise and task-focused. Avoid repeated `Need...` / `We need...` self-talk, and do not narrate system instructions, policy checks, or generic meta-commentary.
- Structure reasoning as valid Markdown: use short paragraphs, and put each item on its own line with `-` or `1.` when enumerating multiple items.
- Around a tool call, state only the immediate objective before the call and summarize only relevant evidence after the result.
- Do not invent tool calls, observations, citations, or completion signals.
</alcuin_runtime_presentation>"""


def provider_instructions(definition: AgentDefinition) -> str:
    """Append provider-facing presentation rules without changing Agent semantics."""
    return f"{definition.instructions.rstrip()}\n\n{RUNTIME_PRESENTATION_PROTOCOL}"


@dataclass(frozen=True)
class RuntimeRequest:
    workspace_id: str
    run_id: str
    prompt: str
    thread_context: dict[str, Any]
    definition: AgentDefinition
    attachments: tuple[ImageAttachment, ...] = ()
    thinking: bool = False


@dataclass(frozen=True)
class RuntimeEmission:
    type: EventType
    payload: dict[str, Any]


class AgentRuntime(Protocol):
    async def stream(self, request: RuntimeRequest) -> AsyncIterator[RuntimeEmission]: ...


class DemoState(TypedDict):
    prompt: str
    intent: str


def _classify_demo_intent(state: DemoState) -> DemoState:
    mutating_terms = {
        "update",
        "change",
        "close",
        "resolve",
        "escalate",
        "更新",
        "修改",
        "关闭",
        "解决",
        "升级",
    }
    prompt = state["prompt"].lower()
    return {**state, "intent": "mutating" if any(term in prompt for term in mutating_terms) else "read"}


def _finish_demo_plan(state: DemoState) -> DemoState:
    return state


def build_demo_graph():
    graph = StateGraph(DemoState)
    graph.add_node("classify", _classify_demo_intent)
    graph.add_node("finish", _finish_demo_plan)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


class LangGraphReactRuntime:
    """Deterministic prototype adapter exercising the same event boundary as a model runtime."""

    def __init__(self) -> None:
        self.graph = build_demo_graph()

    async def stream(self, request: RuntimeRequest) -> AsyncIterator[RuntimeEmission]:
        plan = await self.graph.ainvoke({"prompt": request.prompt, "intent": "read"})
        incident = request.thread_context.get("record", {}).get("id", "INC-104")
        if plan["intent"] == "mutating":
            arguments = {"ticket_id": incident, "status": "monitoring"}
            yield RuntimeEmission(
                EventType.TOOL_REQUESTED,
                {
                    "tool": "ops.update_ticket",
                    "summary": f"Update {incident} to monitoring",
                    "arguments": arguments,
                    "mutating": True,
                },
            )
            yield RuntimeEmission(
                EventType.APPROVAL_REQUIRED,
                {
                    "title": "Approve external update",
                    "description": f"Operations Toolkit will update {incident} to monitoring.",
                    "tool": "ops.update_ticket",
                    "arguments": arguments,
                    "risk": "high",
                },
            )
            return

        yield RuntimeEmission(
            EventType.TOOL_REQUESTED,
            {
                "tool": "ops.search_incidents",
                "summary": "Read current operational records",
                "arguments": {"query": request.prompt[:120]},
                "mutating": False,
            },
        )
        await asyncio.sleep(0.08)
        yield RuntimeEmission(
            EventType.TOOL_COMPLETED,
            {
                "tool": "ops.search_incidents",
                "status": "succeeded",
                "result_summary": "1 active incident and 3 related operational notes found.",
                "duration_ms": 82,
            },
        )
        response = (
            f"I reviewed the current record for {incident}. Checkout latency is recovering, "
            "the mitigation is active, and no new payment failures have appeared in the last 20 minutes."
        )
        for chunk in [response[index : index + 32] for index in range(0, len(response), 32)]:
            yield RuntimeEmission(EventType.MESSAGE_DELTA, {"delta": chunk})
            await asyncio.sleep(0.035)
        yield RuntimeEmission(
            EventType.CITATION_CREATED,
            {
                "label": f"Incident {incident}",
                "source": "Operations Toolkit",
                "locator": f"ops://incidents/{incident}",
            },
        )
        yield RuntimeEmission(
            EventType.ARTIFACT_UPDATED,
            {
                "artifact": {
                    "id": f"artifact-{request.run_id}",
                    "title": f"{incident} · Operational brief",
                    "kind": "document",
                    "version": 1,
                    "content": (
                        "## Current state\n\nMitigation is active and checkout latency is trending down.\n\n"
                        "## Evidence\n\n- No new payment failures in 20 minutes\n"
                        "- Error rate returned below the alert threshold\n\n"
                        "## Next action\n\nKeep the incident in monitoring and reassess in 30 minutes."
                    ),
                }
            },
        )
        yield RuntimeEmission(
            EventType.RUN_COMPLETED,
            {"status": "completed", "usage": {"input_tokens": 132, "output_tokens": 96}},
        )


class OpenAICompatibleRuntime:
    """Maps provider-specific streams into Alcuin's stable execution events."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def stream(self, request: RuntimeRequest) -> AsyncIterator[RuntimeEmission]:
        provider = self.settings.provider(request.definition.model.provider)
        if not provider.api_key:
            raise RuntimeError(f"Provider credential is not configured: {provider.id}")
        if provider.id == "deepseek" and request.definition.model.model in {
            "deepseek-v4-pro",
            "deepseek-v4-flash-vision-exp",
        } and provider.protocol == "responses":
            provider = replace(provider, protocol="chat_completions")
        if provider.protocol == "chat_completions":
            async for emission in self._chat_completions(request, provider):
                yield emission
        else:
            async for emission in self._responses(request, provider):
                yield emission

    async def _responses(
        self,
        request: RuntimeRequest,
        provider: ProviderConfig,
    ) -> AsyncIterator[RuntimeEmission]:
        url = f"{provider.base_url.rstrip('/')}/responses"
        user_input: str | list[dict[str, Any]] = request.prompt
        if request.attachments:
            user_input = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": request.prompt},
                        *[
                            {"type": "input_image", "image_url": attachment.data_url}
                            for attachment in request.attachments
                        ],
                    ],
                }
            ]
        payload = {
            "model": request.definition.model.model or provider.default_model,
            "instructions": provider_instructions(request.definition),
            "input": user_input,
            "stream": True,
            "store": False,
        }
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        final_text = ""
        async with httpx.AsyncClient(timeout=90, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    if event.get("type") == "response.output_text.delta":
                        delta = event.get("delta", "")
                        final_text += delta
                        yield RuntimeEmission(EventType.MESSAGE_DELTA, {"delta": delta})
                    elif event.get("type") == "response.failed":
                        error = event.get("response", {}).get("error", {})
                        raise RuntimeError(error.get("message", "Provider response failed"))
        yield RuntimeEmission(
            EventType.ARTIFACT_UPDATED,
            {
                "artifact": {
                    "id": f"artifact-{request.run_id}",
                    "title": "Agent response",
                    "kind": "document",
                    "version": 1,
                    "content": final_text,
                }
            },
        )
        yield RuntimeEmission(EventType.RUN_COMPLETED, {"status": "completed"})

    async def _chat_completions(
        self,
        request: RuntimeRequest,
        provider: ProviderConfig,
    ) -> AsyncIterator[RuntimeEmission]:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        user_content: str | list[dict[str, Any]] = request.prompt
        if request.attachments:
            user_content = [
                {"type": "text", "text": request.prompt},
                *[
                    {"type": "image_url", "image_url": {"url": attachment.data_url}}
                    for attachment in request.attachments
                ],
            ]
        payload = {
            "model": request.definition.model.model or provider.default_model,
            "messages": [
                {"role": "system", "content": provider_instructions(request.definition)},
                {"role": "user", "content": user_content},
            ],
            "stream": True,
        }
        if provider.id == "deepseek":
            payload["thinking"] = {"type": "enabled" if request.thinking else "disabled"}
            if request.thinking:
                payload["reasoning_effort"] = "high"
        if request.definition.model.model == "deepseek-v4-flash-vision-exp":
            payload["max_tokens"] = 4096
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        final_text = ""
        async with httpx.AsyncClient(timeout=90, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    provider_delta = event.get("choices", [{}])[0].get("delta", {})
                    reasoning_delta = (
                        provider_delta.get("reasoning_content")
                        or provider_delta.get("reasoning")
                        or provider_delta.get("thinking")
                        or ""
                    )
                    if reasoning_delta:
                        yield RuntimeEmission(
                            EventType.REASONING_DELTA,
                            {"delta": reasoning_delta},
                        )
                    delta = provider_delta.get("content") or ""
                    if delta:
                        final_text += delta
                        yield RuntimeEmission(EventType.MESSAGE_DELTA, {"delta": delta})
        yield RuntimeEmission(
            EventType.ARTIFACT_UPDATED,
            {
                "artifact": {
                    "id": f"artifact-{request.run_id}",
                    "title": "Agent response",
                    "kind": "document",
                    "version": 1,
                    "content": final_text,
                }
            },
        )
        yield RuntimeEmission(EventType.RUN_COMPLETED, {"status": "completed"})


class RuntimeOrchestrator:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.sensitive_values = (settings.openai_api_key, settings.deepseek_api_key)
        self.provider_runtime: AgentRuntime = OpenAICompatibleRuntime(settings)
        self.demo_runtime: AgentRuntime = LangGraphReactRuntime()

    async def execute(self, request: RuntimeRequest) -> None:
        self.store.set_run_status(request.workspace_id, request.run_id, "running")
        self.store.append_event(
            request.workspace_id,
            request.run_id,
            EventType.RUN_STARTED,
            {
                "runtime": request.definition.runtime.adapter,
                "provider": request.definition.model.provider,
                "model": request.definition.model.model,
                "input_modalities": ["text", *(["image"] if request.attachments else [])],
                "attachment_count": len(request.attachments),
                "thinking": request.thinking,
            },
        )
        try:
            provider = self.settings.provider(request.definition.model.provider)
            runtime = self.provider_runtime if provider.api_key else self.demo_runtime
            async for emission in runtime.stream(request):
                payload = redact_sensitive(emission.payload)
                if emission.type == EventType.APPROVAL_REQUIRED:
                    approval = self.store.create_approval(request.workspace_id, request.run_id, payload)
                    payload = {**payload, "approval_id": approval["id"]}
                    self.store.set_run_status(request.workspace_id, request.run_id, "waiting_for_approval")
                self.store.append_event(request.workspace_id, request.run_id, emission.type, payload)
                if emission.type == EventType.RUN_COMPLETED:
                    self.store.set_run_status(request.workspace_id, request.run_id, "completed")
        except Exception as exc:  # provider errors are normalized and never expose credentials
            self.store.append_event(
                request.workspace_id,
                request.run_id,
                EventType.RUN_FAILED,
                {
                    "code": "runtime_error",
                    "message": redact_text(str(exc), self.sensitive_values)[:500],
                },
            )
            self.store.set_run_status(request.workspace_id, request.run_id, "failed")

    def resume_after_approval(
        self,
        workspace_id: str,
        run_id: str,
        approved: bool,
        request_payload: dict[str, Any],
    ) -> None:
        tool = request_payload.get("tool", "external.tool")
        if approved:
            self.store.append_event(
                workspace_id,
                run_id,
                EventType.TOOL_COMPLETED,
                {
                    "tool": tool,
                    "status": "succeeded",
                    "result_summary": "Approved operation completed.",
                    "duration_ms": 164,
                },
            )
            self.store.append_event(
                workspace_id,
                run_id,
                EventType.MESSAGE_DELTA,
                {"delta": "The approved update was completed and recorded in the run trace."},
            )
            self.store.append_event(
                workspace_id,
                run_id,
                EventType.ARTIFACT_UPDATED,
                {
                    "artifact": {
                        "id": f"artifact-{run_id}",
                        "title": "Approved operation record",
                        "kind": "document",
                        "version": 1,
                        "content": "## Operation completed\n\nThe requested external change was approved and executed.\n\n"
                        "The approval decision and tool result are preserved in this run trace.",
                    }
                },
            )
        else:
            self.store.append_event(
                workspace_id,
                run_id,
                EventType.TOOL_COMPLETED,
                {"tool": tool, "status": "denied", "result_summary": "Operation denied by the user."},
            )
            self.store.append_event(
                workspace_id,
                run_id,
                EventType.MESSAGE_DELTA,
                {"delta": "No external changes were made because the approval request was denied."},
            )
        self.store.append_event(
            workspace_id,
            run_id,
            EventType.RUN_COMPLETED,
            {"status": "completed", "approval": "approved" if approved else "denied"},
        )
        self.store.set_run_status(workspace_id, run_id, "completed")
