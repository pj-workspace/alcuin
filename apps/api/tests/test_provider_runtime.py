from __future__ import annotations

import json

import httpx
import pytest

from alcuin_api.config import Settings
from alcuin_api.contracts import AgentDefinition, EventType, ImageAttachment
from alcuin_api.runtime import OpenAICompatibleRuntime, RuntimeRequest
from alcuin_api.tools import (
    ToolCitation,
    ToolContext,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)


@pytest.mark.asyncio
async def test_deepseek_responses_stream_maps_to_execution_events() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/responses"
        assert request.headers["authorization"] == "Bearer ds-test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["stream"] is True
        assert payload["instructions"].startswith("Answer clearly and briefly.")
        assert "Match the user's language" in payload["instructions"]
        assert "valid Markdown" in payload["instructions"]
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
                'data: {"type":"response.output_text.delta","delta":" from DeepSeek"}\n\n'
                'data: {"type":"response.completed"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    settings = Settings(
        database_path=":memory:",
        deepseek_api_key="ds-test-key",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-flash",
        deepseek_protocol="responses",
    )
    runtime = OpenAICompatibleRuntime(settings, httpx.MockTransport(handler))
    request = RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_test",
        prompt="Say hello",
        thread_context={},
        definition=AgentDefinition(
            identity={"name": "DeepSeek Agent"},
            instructions="Answer clearly and briefly.",
            model={
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "credential_ref": "secret://workspace/deepseek-primary",
            },
        ),
    )

    emissions = [emission async for emission in runtime.stream(request)]

    assert [emission.type for emission in emissions] == [
        EventType.MESSAGE_DELTA,
        EventType.MESSAGE_DELTA,
        EventType.ARTIFACT_UPDATED,
        EventType.RUN_COMPLETED,
    ]
    assert emissions[-2].payload["artifact"]["content"] == "Hello from DeepSeek"


@pytest.mark.asyncio
async def test_deepseek_vision_uses_chat_completions_image_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4-flash-vision-exp"
        content = payload["messages"][1]["content"]
        assert payload["stream"] is True
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["messages"][0]["content"].startswith(
            "Inspect images carefully and answer briefly."
        )
        assert "provider-visible reasoning" in payload["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "What color is this?"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"reasoning_content":"checking"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"bl"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"ue"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    settings = Settings(
        database_path=":memory:",
        deepseek_api_key="ds-test-key",
        deepseek_model="deepseek-v4-flash-vision-exp",
        deepseek_protocol="responses",
    )
    runtime = OpenAICompatibleRuntime(settings, httpx.MockTransport(handler))
    request = RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_vision",
        prompt="What color is this?",
        thread_context={},
        definition=AgentDefinition(
            identity={"name": "Vision Agent"},
            instructions="Inspect images carefully and answer briefly.",
            model={
                "provider": "deepseek",
                "model": "deepseek-v4-flash-vision-exp",
                "credential_ref": "secret://workspace/deepseek-primary",
            },
        ),
        attachments=(
            ImageAttachment(
                name="sample.png",
                media_type="image/png",
                data_url="data:image/png;base64,iVBORw0KGgo=",
            ),
        ),
    )

    emissions = [emission async for emission in runtime.stream(request)]

    reasoning = [
        emission.payload["delta"]
        for emission in emissions
        if emission.type == EventType.REASONING_DELTA
    ]
    assert reasoning == ["checking"]
    assert emissions[-2].payload["artifact"]["content"] == "blue"


@pytest.mark.asyncio
async def test_deepseek_thinking_mode_is_explicit_and_streamed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "high"
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"reasoning_content":"inspect"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"done"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    runtime = OpenAICompatibleRuntime(
        Settings(database_path=":memory:", deepseek_api_key="ds-test-key"),
        httpx.MockTransport(handler),
    )
    request = RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_thinking",
        prompt="Inspect this",
        thread_context={},
        definition=AgentDefinition(
            identity={"name": "Thinking Agent"},
            instructions="Think carefully, then answer.",
            model={"provider": "deepseek", "model": "deepseek-v4-flash-vision-exp"},
        ),
        thinking=True,
    )

    emissions = [emission async for emission in runtime.stream(request)]

    assert [emission.type for emission in emissions[:2]] == [
        EventType.REASONING_DELTA,
        EventType.MESSAGE_DELTA,
    ]


@pytest.mark.asyncio
async def test_chat_completions_executes_tool_and_continues_to_final_answer() -> None:
    requests: list[dict] = []
    seen_contexts: list[ToolContext] = []

    async def provider_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        if len(requests) == 1:
            assert payload["tools"][0]["function"]["name"] == "knowledge_search"
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"knowledge_search","arguments":"{\\"query\\":\\"Alc"}}]}}]}\n\n'
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"uin\\"}"}}]}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )

        tool_message = payload["messages"][-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "call_1"
        assert json.loads(tool_message["content"])["hits"][0]["title"] == "Alcuin"
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Alcuin is extensible."}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async def knowledge_search(context: ToolContext, arguments: dict) -> ToolResult:
        seen_contexts.append(context)
        return ToolResult(
            data={"hits": [{"title": "Alcuin", "text": arguments["query"]}]},
            summary="1 knowledge hit",
            citations=(
                ToolCitation(
                    label="Architecture",
                    source="knowledge",
                    locator="kb://docs/alcuin",
                ),
            ),
        )

    tool = ToolDefinition(
        name="knowledge.search",
        description="Search governed workspace knowledge",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=knowledge_search,
    )
    runtime = OpenAICompatibleRuntime(
        Settings(
            database_path=":memory:",
            deepseek_api_key="ds-test-key",
            deepseek_protocol="responses",
        ),
        httpx.MockTransport(provider_handler),
        ToolExecutor(ToolRegistry([tool])),
    )
    request = RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_tools",
        prompt="What is Alcuin?",
        thread_context={"record": {"id": "DOC-1"}},
        definition=AgentDefinition(
            identity={"name": "Research Agent"},
            instructions="Search before answering questions about the product.",
            model={"provider": "deepseek", "model": "deepseek-v4-flash-vision-exp"},
            tools=["knowledge.search"],
            runtime={"adapter": "langgraph-react", "max_steps": 4},
        ),
    )

    emissions = [emission async for emission in runtime.stream(request)]

    assert [emission.type for emission in emissions] == [
        EventType.TOOL_REQUESTED,
        EventType.TOOL_COMPLETED,
        EventType.CITATION_CREATED,
        EventType.MESSAGE_DELTA,
        EventType.ARTIFACT_UPDATED,
        EventType.RUN_COMPLETED,
    ]
    assert emissions[0].payload["arguments"] == {"query": "Alcuin"}
    assert emissions[1].payload["result_summary"] == "1 knowledge hit"
    assert emissions[2].payload["locator"] == "kb://docs/alcuin"
    assert emissions[-2].payload["artifact"]["content"] == "Alcuin is extensible."
    assert seen_contexts[0].workspace_id == "ws_test"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_tool_loop_stops_at_agent_max_steps() -> None:
    async def provider_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"loop","function":{"name":"test_loop","arguments":"{}"}}]}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async def loop_tool(_context: ToolContext, _arguments: dict) -> ToolResult:
        return ToolResult(data={"continue": True}, summary="continue")

    runtime = OpenAICompatibleRuntime(
        Settings(database_path=":memory:", deepseek_api_key="ds-test-key"),
        httpx.MockTransport(provider_handler),
        ToolExecutor(
            ToolRegistry(
                [
                    ToolDefinition(
                        name="test.loop",
                        description="Loop forever",
                        input_schema={"type": "object", "additionalProperties": False},
                        handler=loop_tool,
                    )
                ]
            )
        ),
    )
    request = RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_loop",
        prompt="Loop",
        thread_context={},
        definition=AgentDefinition(
            identity={"name": "Loop Agent"},
            instructions="Use the loop tool when asked.",
            model={"provider": "deepseek", "model": "deepseek-v4-flash-vision-exp"},
            tools=["test.loop"],
            runtime={"adapter": "langgraph-react", "max_steps": 1},
        ),
    )

    with pytest.raises(RuntimeError, match="max_steps=1"):
        _ = [emission async for emission in runtime.stream(request)]
