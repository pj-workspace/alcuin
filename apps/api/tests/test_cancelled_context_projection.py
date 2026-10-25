"""Cancelled output remains partial in model context, including compacted history."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from alcuin_api.config import Settings
from alcuin_api import context_composition as composition
from alcuin_context import ContextMessage
from alcuin_context.assembly import compaction_source_digest
from alcuin_core.contracts import AgentDefinition


def records():
    return [
        {"id": "user1", "sequence": 1, "role": "user", "run_id": "run1", "status": "completed", "parts": [{"type": "text", "text": "Prepare the report"}]},
        {"id": "partial1", "sequence": 2, "role": "assistant", "run_id": "run1", "status": "cancelled", "estimated_tokens": 1, "parts": [{"type": "text", "text": "The first finding is"}]},
        {"id": "user2", "sequence": 3, "role": "user", "run_id": "run2", "status": "completed", "parts": [{"type": "text", "text": "What happened?"}]},
    ]


def test_cancelled_projection_does_not_rewrite_persisted_message():
    message = records()[1]
    original = deepcopy(message)
    projected = composition.message_content(message)
    assert "incomplete" in projected
    assert "Do not automatically retry previously started actions" in projected
    assert "The first finding is" in projected
    assert message == original
    message["status"] = "completed"
    assert composition.message_content(message) == "The first finding is"


@pytest.mark.asyncio
@pytest.mark.parametrize("compacted", [False, True])
@pytest.mark.parametrize("partial_text", ["The first finding is", ""])
async def test_cancelled_context_counts_notice_and_keeps_status_outside_summary(monkeypatch, compacted, partial_text):
    source = records()
    source[1]["parts"] = [{"type": "text", "text": partial_text}]
    original = deepcopy(source)
    history = tuple(ContextMessage(id=row["id"], sequence=row["sequence"], role=row["role"], content=composition.message_content(row)) for row in source[:2])
    active = {
        "id": "summary1", "through_sequence": 2,
        # Deliberately simulate a truncated summary that lost its inline marker.
        "summary": "Assistant: The first finding is",
        "source_message_ids": ["user1", "partial1"],
        "source_digest": compaction_source_digest(history), "strategy": "extractive-turns-v1",
    } if compacted else None
    repository = SimpleNamespace(
        list_thread_input_answers=lambda *args: [],
        get_run_customization_snapshot=lambda *args: None,
        get_active_compaction=lambda *args: active,
    )
    monkeypatch.setattr(composition, "load_thread_messages", lambda *args, **kwargs: source)
    monkeypatch.setattr(composition, "resolve_customization_context", lambda *args, **kwargs: SimpleNamespace(sections=[], thread_configuration_revision=0, workspace_preferences_revision=None))
    composer = composition.RunContextComposer(repository, Settings())
    result = await composer.compose(
        workspace_id="ws_demo", run={"id": "run2", "agent_version_id": "v1", "input_message_id": "user2"},
        thread={"id": "thread1", "context": {}},
        definition=AgentDefinition.model_validate({"identity": {"name": "Test agent"}, "instructions": "Use the supplied evidence."}),
        platform_protocol="Be accurate.",
    )
    assert composition.CANCELLED_OUTPUT_NOTICE in result.assembly.system_prompt
    assert "partial1" in result.assembly.system_prompt
    assert source == original
    if compacted:
        assert result.assembly.compaction_id == "summary1"
        assert [message.id for message in result.assembly.messages] == ["user2"]
    else:
        partial = next(message for message in result.assembly.messages if message.id == "partial1")
        assert "Do not automatically retry previously started actions" in partial.content
        assert partial.estimated_tokens is None
        trace = next(entry for entry in result.assembly.trace if entry.id == "partial1")
        assert trace.estimated_tokens == composer.estimator.estimate_text(partial.content) + 4
        assert trace.estimated_tokens > original[1]["estimated_tokens"] + 4
