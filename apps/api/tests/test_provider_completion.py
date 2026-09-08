"""Provider output limits and completion signals must agree with durable Run state."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from alcuin_api.config import Settings
from alcuin_api.runtime import OpenAICompatibleRuntime, RuntimeRequest
from alcuin_core.contracts import AgentDefinition, EventType


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None, openai_api_key="test-key", deepseek_api_key="test-key", **kwargs
    )


def _request(
    *, provider="deepseek", model="deepseek-v4-flash-vision-exp"
) -> RuntimeRequest:
    return RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_completion",
        prompt="Generate the requested result",
        thread_context={},
        definition=AgentDefinition(
            identity={"name": "Completion Agent"},
            instructions="Return the requested deliverable.",
            model={"provider": provider, "model": model},
        ),
        thinking=True,
        reasoning_effort="high",
    )


def _sse(*events, done=True) -> str:
    frames = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return frames + ("data: [DONE]\n\n" if done else "")


async def _emissions(body: str, *, responses=False, payloads=None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if payloads is not None:
            payloads.append(json.loads(request.content))
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    runtime = OpenAICompatibleRuntime(
        _settings(provider_mode="responses" if responses else "chat_completions"),
        httpx.MockTransport(handler),
    )
    return [
        event
        async for event in runtime.stream(
            _request(provider="openai", model="gpt-4.1-mini")
            if responses
            else _request()
        )
    ]


def _assert_failed(events, code):
    assert events[-1].type is EventType.RUN_FAILED
    assert events[-1].payload["code"] == code
    assert all(event.type is not EventType.RUN_COMPLETED for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "length"])
async def test_reasoning_only_never_completes_as_a_successful_answer(
    reason: str,
) -> None:
    events = await _emissions(
        _sse(
            {"choices": [{"delta": {"reasoning_content": "Checking the material."}}]},
            {"choices": [{"delta": {}, "finish_reason": reason}]},
        )
    )
    assert events[0].type is EventType.REASONING_DELTA
    _assert_failed(
        events,
        "provider_output_limit" if reason == "length" else "provider_empty_output",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,code",
    [
        ("length", "provider_output_limit"),
        ("content_filter", "provider_content_filtered"),
        ("error", "provider_response_failed"),
    ],
)
async def test_chat_partial_answer_survives_unsuccessful_finish(
    reason: str, code: str
) -> None:
    events = await _emissions(
        _sse(
            {"choices": [{"delta": {"content": "Partial answer"}}]},
            {"choices": [{"delta": {}, "finish_reason": reason}]},
        )
    )
    assert events[0].type is EventType.MESSAGE_DELTA
    assert events[0].payload["delta"] == "Partial answer"
    _assert_failed(events, code)


@pytest.mark.asyncio
async def test_chat_partial_artifact_survives_output_limit_without_becoming_completed() -> (
    None
):
    events = await _emissions(
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "content": '<alcuin-answer><alcuin-artifact title="Report" content-type="text/markdown"># Partial report'
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        )
    )
    artifact = next(
        event for event in events if event.type is EventType.ARTIFACT_UPDATED
    )
    assert artifact.payload["artifact"]["content"] == "# Partial report"
    assert artifact.payload["streaming"] is True
    _assert_failed(events, "provider_output_limit")


@pytest.mark.asyncio
async def test_provider_error_frame_does_not_leak_its_raw_message() -> None:
    events = await _emissions(
        _sse(
            {"error": {"message": "Authorization test-key private response"}},
            done=False,
        )
    )
    _assert_failed(events, "provider_response_failed")
    assert "test-key" not in json.dumps([event.payload for event in events])


@pytest.mark.asyncio
async def test_transport_eof_without_a_terminal_signal_preserves_partial_but_fails() -> (
    None
):
    events = await _emissions(
        _sse({"choices": [{"delta": {"content": "Partial answer"}}]}, done=False)
    )
    assert events[0].payload["delta"] == "Partial answer"
    _assert_failed(events, "provider_incomplete_stream")


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["   ", "<alcuin-answer></alcuin-answer>"])
async def test_empty_final_block_is_not_a_completed_answer(content: str) -> None:
    events = await _emissions(
        _sse({"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]})
    )
    _assert_failed(events, "provider_empty_output")


@pytest.mark.asyncio
async def test_normal_chat_completion_ignores_usage_only_chunks() -> None:
    events = await _emissions(
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "content": "<alcuin-answer>Complete answer.</alcuin-answer>"
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            {"choices": [], "usage": {"completion_tokens": 12}},
        )
    )
    assert events[-1].type is EventType.RUN_COMPLETED
    assert (
        "".join(
            event.payload.get("delta", "")
            for event in events
            if event.type is EventType.MESSAGE_DELTA
        )
        == "Complete answer."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,reason,code",
    [
        ("response.incomplete", "max_output_tokens", "provider_output_limit"),
        ("response.incomplete", "content_filter", "provider_content_filtered"),
        ("response.failed", None, "provider_response_failed"),
        ("error", None, "provider_response_failed"),
    ],
)
async def test_responses_protocol_preserves_partial_output_and_normalizes_failure(
    event_type, reason, code
) -> None:
    events = await _emissions(
        _sse(
            {"type": "response.output_text.delta", "delta": "Partial response."},
            {
                "type": event_type,
                "response": {
                    "incomplete_details": {"reason": reason},
                    "error": {"message": "secret-provider-message"},
                },
            },
        ),
        responses=True,
    )
    assert events[0].payload["delta"] == "Partial response."
    _assert_failed(events, code)
    assert "secret-provider-message" not in json.dumps(
        [event.payload for event in events]
    )


@pytest.mark.asyncio
async def test_empty_responses_completion_is_a_failure() -> None:
    events = await _emissions(
        _sse({"type": "response.completed", "response": {"status": "completed"}}),
        responses=True,
    )
    _assert_failed(events, "provider_empty_output")


@pytest.mark.asyncio
async def test_responses_done_trailer_without_response_completed_is_incomplete() -> (
    None
):
    events = await _emissions(
        _sse({"type": "response.output_text.delta", "delta": "Partial response"}),
        responses=True,
    )
    assert events[0].payload["delta"] == "Partial response"
    _assert_failed(events, "provider_incomplete_stream")


@pytest.mark.asyncio
async def test_responses_completed_event_cannot_hide_incomplete_response_status() -> (
    None
):
    events = await _emissions(
        _sse(
            {"type": "response.output_text.delta", "delta": "Partial response"},
            {
                "type": "response.completed",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            },
        ),
        responses=True,
    )
    _assert_failed(events, "provider_output_limit")


@pytest.mark.asyncio
async def test_chat_later_success_trailer_cannot_erase_output_limit() -> None:
    events = await _emissions(
        _sse(
            {"choices": [{"delta": {"content": "Partial"}, "finish_reason": "length"}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )
    )
    _assert_failed(events, "provider_output_limit")


@pytest.mark.asyncio
async def test_truncated_tool_call_is_never_dispatched() -> None:
    from alcuin_api.tools import ToolDefinition, ToolExecutor, ToolRegistry, ToolResult

    calls = []

    async def handler_tool(arguments, context):
        calls.append(arguments)
        return ToolResult(summary="Should not execute", data={})

    async def handler_http(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "knowledge_search",
                                            "arguments": '{"query":"x"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "length",
                        }
                    ]
                }
            ),
        )

    tool = ToolDefinition(
        name="knowledge.search",
        description="Search governed knowledge",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=handler_tool,
    )
    base = _request()
    request = RuntimeRequest(
        workspace_id=base.workspace_id,
        run_id=base.run_id,
        prompt=base.prompt,
        thread_context={},
        definition=base.definition.model_copy(update={"tools": ["knowledge.search"]}),
    )
    runtime = OpenAICompatibleRuntime(
        _settings(),
        httpx.MockTransport(handler_http),
        ToolExecutor(ToolRegistry([tool])),
    )
    events = [event async for event in runtime.stream(request)]
    _assert_failed(events, "provider_output_limit")
    assert calls == []
    assert all(event.type is not EventType.TOOL_REQUESTED for event in events)


@pytest.mark.asyncio
async def test_normal_responses_completion_succeeds() -> None:
    events = await _emissions(
        _sse(
            {"type": "response.output_text.delta", "delta": "A complete answer."},
            {"type": "response.completed", "response": {"status": "completed"}},
        ),
        responses=True,
    )
    assert events[-1].type is EventType.RUN_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", ["deepseek-v4-flash-vision-exp", "deepseek-v4-flash", "deepseek-v4-pro"]
)
@pytest.mark.parametrize("budget", [None, 2048])
async def test_all_chat_models_use_the_context_output_budget_without_changing_profile(
    model, budget
) -> None:
    seen = []
    settings = _settings(
        **({"context_reserved_output_tokens": budget} if budget is not None else {})
    )

    async def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=_sse(
                {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]}
            ),
        )

    runtime = OpenAICompatibleRuntime(settings, httpx.MockTransport(handler))
    events = [event async for event in runtime.stream(_request(model=model))]
    assert events[-1].type is EventType.RUN_COMPLETED
    assert (
        seen[0]["max_tokens"]
        == settings.context_reserved_output_tokens
        == (budget or 16_384)
    )
    assert seen[0]["model"] == model
    assert seen[0]["reasoning_effort"] == "high"
    assert seen[0]["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_responses_budget_matches_reserved_context_output_tokens() -> None:
    payloads = []
    await _emissions(
        _sse(
            {"type": "response.output_text.delta", "delta": "Done"},
            {"type": "response.completed"},
        ),
        responses=True,
        payloads=payloads,
    )
    assert payloads[0]["max_output_tokens"] == 16_384


def test_small_context_windows_scale_only_implicit_reserves() -> None:
    settings = _settings(context_window_tokens=8192)
    assert 256 <= settings.context_reserved_output_tokens < 8192
    assert (
        settings.context_reserved_output_tokens + settings.context_reserved_tool_tokens
        < 8192
    )
    explicit = _settings(
        context_window_tokens=8192,
        context_reserved_output_tokens=4096,
        context_reserved_tool_tokens=1024,
    )
    assert explicit.context_reserved_output_tokens == 4096
    assert explicit.context_reserved_tool_tokens == 1024
    with pytest.raises(ValidationError, match="leave capacity"):
        _settings(
            context_window_tokens=8192,
            context_reserved_output_tokens=8192,
            context_reserved_tool_tokens=0,
        )
