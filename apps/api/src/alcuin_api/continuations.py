"""Private model-loop checkpoints shared by human interaction boundaries.

Never expose these transcripts in execution events. Storage remains responsible
for ownership, size limits, and atomically claiming the suspended Run.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from typing import Any

from .security import redact_sensitive, redact_text


def prepare_continuation(
    continuation: dict[str, Any],
    request_profile: dict[str, Any],
    sensitive_values: Iterable[str | None],
) -> dict[str, Any]:
    """Copy and sanitize before persistence without changing the live transcript.

    Decode tool JSON before redaction: replacing secrets in serialized JSON can
    miss escaped quotes/newlines or corrupt a JSON escape sequence. Keep model
    message structure and budget fields intact; secret-shaped field filtering
    applies to tool data, not protocol keys such as token budgets.
    """
    secrets = tuple(sorted({value for value in sensitive_values if value}, key=len, reverse=True))

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value, secrets)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {redact_text(key, secrets): clean(item) for key, item in value.items()}
        return value

    def tool_json(value: str) -> str:
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return clean(value)
        return json.dumps(clean(redact_sensitive(decoded)), ensure_ascii=False)

    checkpoint = copy.deepcopy(continuation)
    for message in checkpoint["messages"]:
        if message.get("role") == "tool":
            message["content"] = tool_json(message["content"])
        for call in message.get("tool_calls") or []:
            call["function"]["arguments"] = tool_json(call["function"]["arguments"])
    checkpoint["request"] = copy.deepcopy(request_profile)
    return clean(checkpoint)
