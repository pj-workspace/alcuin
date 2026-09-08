from __future__ import annotations

import asyncio

import pytest

from alcuin_api.tools import (
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)


def _definition(handler, *, timeout_seconds: float = 1.0) -> ToolDefinition:
    return ToolDefinition(
        name="test.lookup",
        description="Look up a record",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_tool_executor_enforces_allowlist_schema_and_workspace_context() -> None:
    seen: list[ToolContext] = []

    async def handler(context: ToolContext, arguments: dict) -> ToolResult:
        seen.append(context)
        return ToolResult(data={"value": arguments["query"]}, summary="Record found")

    executor = ToolExecutor(ToolRegistry([_definition(handler)]))
    context = ToolContext(
        workspace_id="ws_alpha",
        run_id="run_1",
        thread_context={"record": {"id": "A-1"}},
    )

    execution = await executor.execute(
        "test.lookup",
        {"query": "alpha"},
        allowed_names=["test.lookup"],
        context=context,
    )

    assert execution.result.data == {"value": "alpha"}
    assert seen == [context]

    with pytest.raises(ToolError, match="not available") as denied:
        await executor.execute(
            "test.lookup",
            {"query": "alpha"},
            allowed_names=[],
            context=context,
        )
    assert denied.value.code == "tool_not_allowed"

    with pytest.raises(ToolError) as invalid:
        await executor.execute(
            "test.lookup",
            {"query": "", "unexpected": True},
            allowed_names=["test.lookup"],
            context=context,
        )
    assert invalid.value.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_tool_executor_enforces_deadline_without_exposing_internal_error() -> None:
    async def slow_handler(_context: ToolContext, _arguments: dict) -> ToolResult:
        await asyncio.sleep(0.2)
        return ToolResult(data={}, summary="late")

    executor = ToolExecutor(
        ToolRegistry([_definition(slow_handler, timeout_seconds=0.1)])
    )

    with pytest.raises(ToolError) as timed_out:
        await executor.execute(
            "test.lookup",
            {"query": "alpha"},
            allowed_names=["test.lookup"],
            context=ToolContext("ws_alpha", "run_1", {}),
        )

    assert timed_out.value.code == "tool_timeout"
    assert "deadline" in timed_out.value.message


@pytest.mark.asyncio
async def test_tool_executor_hides_uncontrolled_handler_errors() -> None:
    async def broken_handler(_context: ToolContext, _arguments: dict) -> ToolResult:
        raise RuntimeError("credential-value-must-not-leak")

    executor = ToolExecutor(ToolRegistry([_definition(broken_handler)]))

    with pytest.raises(ToolError) as failed:
        await executor.execute(
            "test.lookup",
            {"query": "alpha"},
            allowed_names=["test.lookup"],
            context=ToolContext("ws_alpha", "run_1", {}),
        )

    assert failed.value.code == "tool_failed"
    assert failed.value.message == "Tool execution failed"
    assert "credential-value" not in failed.value.message
