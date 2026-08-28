from __future__ import annotations

from typing import Any

from alcuin_api.tools import ToolCitation, ToolContext, ToolError, ToolResult


async def operations_demo_adapter(
    context: ToolContext,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Trusted in-process adapter for the explicit Operations Toolkit demo."""
    if tool_name == "ops.search_incidents":
        return ToolResult(
            data={
                "query": str(arguments.get("query", "")),
                "incidents": [
                    {
                        "id": context.thread_context.get("record", {}).get(
                            "id", "INC-104"
                        ),
                        "status": "monitoring",
                    }
                ],
            },
            summary="One matching incident found",
            citations=(
                ToolCitation(
                    label="Active incident",
                    source="Operations Toolkit",
                    locator=(
                        "ops://incidents/"
                        + str(
                            context.thread_context.get("record", {}).get(
                                "id", "INC-104"
                            )
                        )
                    ),
                ),
            ),
        )
    if tool_name == "ops.update_ticket":
        return ToolResult(
            data={
                "ticket_id": str(arguments["ticket_id"]),
                "status": str(arguments["status"]),
                "updated": True,
            },
            summary="Incident ticket updated",
        )
    raise ToolError("tool_not_allowed", "Operations Toolkit does not expose this tool")
