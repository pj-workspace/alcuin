import copy
import json

import pytest

from alcuin_api.continuations import prepare_continuation


@pytest.mark.parametrize("secret", ['quoted"value', "line\nbreak", r"back\slash", "凭据内容", "plain-value"])
def test_checkpoint_redacts_decoded_values_and_tool_json_without_mutation(secret):
    checkpoint = {
        "adapter": "openai-chat-v1", "next_step": 4,
        "tool_call_counts": {"human.ask": 1, "web.search": 2},
        "messages": [
            {"role": "system", "content": f"Never repeat {secret}"},
            {"role": "assistant", "tool_calls": [{"id": "call1", "function": {
                "name": "some_tool", "arguments": json.dumps({"query": secret, "password": "unknown-value", "nested": [{"value": secret}]}),
            }}]},
            {"role": "tool", "tool_call_id": "call1", "content": json.dumps([{"value": secret, "api_key": "another-value"}])},
        ],
    }
    original = copy.deepcopy(checkpoint)
    profile = {"effective_model": "model", "current_message_id": "msg1", "attachments": [], "note": secret}
    result = prepare_continuation(checkpoint, profile, [secret, None, ""])
    assert checkpoint == original
    assert profile["note"] == secret
    assert result["next_step"] == 4
    assert result["tool_call_counts"] == checkpoint["tool_call_counts"]
    assert result["request"]["note"] == "[REDACTED]"
    assert result["messages"][0]["content"] == "Never repeat [REDACTED]"
    arguments = json.loads(result["messages"][1]["tool_calls"][0]["function"]["arguments"])
    assert arguments == {"query": "[REDACTED]", "password": "[REDACTED]", "nested": [{"value": "[REDACTED]"}]}
    assert json.loads(result["messages"][2]["content"]) == [{"value": "[REDACTED]", "api_key": "[REDACTED]"}]
    assert json.loads(json.dumps(result)) == result


def test_checkpoint_preserves_image_references_and_handles_non_json_tool_result():
    messages = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "alcuin-attachment://att1"}}]},
        {"role": "tool", "content": "not JSON: sensitive-long"},
    ]
    checkpoint = {"adapter": "openai-chat-v1", "messages": messages, "next_step": 2}
    result = prepare_continuation(checkpoint, {"attachments": [{"id": "att1"}]}, ["sensitive", "sensitive-long"])
    assert result["messages"][0] == messages[0]
    assert result["messages"][1]["content"] == "not JSON: [REDACTED]"
    assert result["request"]["attachments"] == [{"id": "att1"}]
