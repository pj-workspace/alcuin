from __future__ import annotations

from alcuin_api.security import redact_sensitive, redact_text


def test_redacts_secret_shaped_tool_arguments_and_error_values() -> None:
    payload = {
        "tool": "ops.lookup",
        "arguments": {
            "query": "incident",
            "apiKey": "raw-key",
            "nested": {"authorization": "Bearer raw-token"},
        },
    }

    assert redact_sensitive(payload) == {
        "tool": "ops.lookup",
        "arguments": {
            "query": "incident",
            "apiKey": "[REDACTED]",
            "nested": {"authorization": "[REDACTED]"},
        },
    }
    assert redact_text("provider rejected raw-key", ["raw-key"]) == "provider rejected [REDACTED]"
