from __future__ import annotations

import json

import httpx
import pytest

from alcuin_api.config import Settings
from alcuin_api.runtime import OpenAICompatibleRuntime, RuntimeRequest
from alcuin_api.tools import ToolDefinition, ToolExecutor, ToolRegistry, ToolResult
from alcuin_core.contracts import AgentDefinition, EventType


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,with_tools",
    [("responses", False), ("chat_completions", False), ("chat_completions", True)],
)
async def test_artifacts_stream_before_provider_completion_and_do_not_duplicate_chat(
    protocol, with_tools
):
    consumed_all = False
    chunks = [
        "<alcuin-",
        "answer>已生成交互页面和说明。",
        '<alcuin-artifact title="交互页面" content-type="text/html">',
        '<!doctype html><button onclick="this.textContent=123">试一下</button>',
        "</alcuin-artifact>",
        '<alcuin-artifact title="使用说明" content-type="text/markdown">',
        "# 使用说明\n\n- 点击按钮。\n- 查看结果。",
        "</alcuin-artifact>可以在画布中修改。</alcuin-answer>",
    ]

    class ProviderStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal consumed_all
            for chunk in chunks:
                body = (
                    {"type": "response.output_text.delta", "delta": chunk}
                    if protocol == "responses"
                    else {"choices": [{"delta": {"content": chunk}}]}
                )
                yield (
                    "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"
                ).encode()
            consumed_all = True
            if protocol == "responses":
                yield b'data: {"type": "response.completed"}\n\n'
            yield b"data: [DONE]\n\n"

    async def unused_tool(_context, _arguments):
        return ToolResult(data={}, summary="Unused")

    def handler(request):
        if with_tools:
            assert json.loads(request.content)["tools"]
        return httpx.Response(
            200, stream=ProviderStream(), headers={"content-type": "text/event-stream"}
        )

    runtime = OpenAICompatibleRuntime(
        Settings(deepseek_api_key="test", deepseek_protocol=protocol),
        httpx.MockTransport(handler),
        ToolExecutor(
            ToolRegistry(
                [
                    ToolDefinition(
                        name="test.read",
                        description="Read",
                        input_schema={"type": "object"},
                        handler=unused_tool,
                    )
                ]
            )
        ),
    )
    request = RuntimeRequest(
        workspace_id="ws_test",
        run_id="run_artifacts",
        prompt="生成页面和说明",
        thread_context={},
        definition=AgentDefinition(
            identity={"name": "Agent"},
            instructions="Help the user.",
            model={"provider": "deepseek", "model": "deepseek-v4-flash"},
            tools=["test.read"] if with_tools else [],
        ),
    )
    artifacts = {}
    visible = ""
    seen_live = False
    events = []
    async for event in runtime.stream(request):
        events.append(event)
        if event.type == EventType.MESSAGE_DELTA:
            visible += event.payload["delta"]
        if event.type == EventType.ARTIFACT_UPDATED:
            seen_live |= not consumed_all
            artifacts[event.payload["artifact"]["generation_key"]] = event.payload[
                "artifact"
            ]
    assert seen_live, (
        "Artifacts must be emitted while provider is still producing content"
    )
    assert visible == "已生成交互页面和说明。可以在画布中修改。"
    assert len(artifacts) == 2
    assert artifacts["1"]["content"].startswith("<!doctype html>")
    assert artifacts["2"]["content"].startswith("# 使用说明")
    assert events[-1].type == EventType.RUN_COMPLETED
