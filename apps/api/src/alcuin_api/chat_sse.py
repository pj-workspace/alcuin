from __future__ import annotations

from typing import Any

from alcuin_core.contracts import EventType


def project_execution_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Project canonical execution events into Alcuin's compact chat stream."""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    common = {
        "eventId": event["id"],
        "runId": event["run_id"],
        "sequence": event["sequence"],
        "timestamp": event["timestamp"],
    }
    event_type = event["type"]
    if event_type == EventType.RUN_STARTED:
        return [{
            **common,
            "type": "meta",
            "provider": payload.get("provider"),
            "chatModel": payload.get("model"),
            "runtime": payload.get("runtime"),
            "thinking": payload.get("thinking", False),
            "reasoningEffort": payload.get("reasoning_effort"),
            "inputModalities": payload.get("input_modalities", ["text"]),
            "attachmentCount": payload.get("attachment_count", 0),
        }]
    if event_type == EventType.REASONING_DELTA:
        return [{**common, "type": "thinking-delta", "textDelta": payload.get("delta", "")}]
    if event_type == EventType.MESSAGE_DELTA:
        return [{**common, "type": "text-delta", "textDelta": payload.get("delta", "")}]
    if event_type == EventType.TOOL_REQUESTED:
        return [{
            **common,
            "type": "tool-call",
            "name": payload.get("tool", "tool"),
            "input": payload.get("arguments", {}),
            "summary": payload.get("summary", "Tool requested"),
            "mutating": payload.get("mutating", False),
        }]
    if event_type == EventType.TOOL_COMPLETED:
        return [{
            **common,
            "type": "tool-result",
            "name": payload.get("tool", "tool"),
            "status": "success" if payload.get("status") == "succeeded" else payload.get("status", "error"),
            "outputPreview": payload.get("result_summary", ""),
        }]
    if event_type == EventType.APPROVAL_REQUIRED:
        return [{**common, "type": "approval-required", **payload}]
    if event_type == EventType.ARTIFACT_UPDATED:
        return [{**common, "type": "artifact-updated", **payload}]
    if event_type == EventType.CITATION_CREATED:
        return [{**common, "type": "source-registry", "sources": [payload]}]
    if event_type == EventType.RUN_FAILED:
        return [{**common, "type": "error", "message": payload.get("message", "Run failed")}]
    if event_type == EventType.RUN_COMPLETED:
        return [{**common, "type": "done"}]
    return []
