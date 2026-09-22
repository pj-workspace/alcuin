"""Intrinsic, non-mutating conversation interaction exposed as a tool."""
from alcuin_core.human_input import HumanQuestion
from .tools import ToolContext, ToolDefinition, ToolError, ToolResult


async def _question_requires_runtime(context: ToolContext, arguments: dict) -> ToolResult:
    raise ToolError("interaction_unavailable", "Questions must use the interactive runtime")


def human_question_tool() -> ToolDefinition:
    return ToolDefinition(
        name="human.ask",
        description=(
            "Ask the user one necessary clarification and pause until they answer. "
            "Use only when missing information materially affects the result; otherwise proceed. "
            "Write the question and optional choices in the user's language. Free text is always allowed. "
            "Never request passwords, API keys, or other credentials. "
            "This is not permission to perform an external action. "
            "If the user skips, use a clearly stated conservative assumption instead of asking again."
        ),
        input_schema=HumanQuestion.model_json_schema(),
        handler=_question_requires_runtime,
        max_calls_per_run=3,
        interaction="question",
    )
