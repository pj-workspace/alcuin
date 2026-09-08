from __future__ import annotations

import pytest

from alcuin_context import (
    CompactionRecord,
    ContextAssembler,
    ContextAssemblyRequest,
    ContextBudgetExceeded,
    ContextLayer,
    ContextMessage,
    ContextSection,
    ExtractiveContextCompactor,
    HeuristicTokenEstimator,
    TokenBudget,
    compaction_source_digest,
)


def message(sequence: int, role: str, content: str) -> ContextMessage:
    return ContextMessage(
        id=f"msg_{sequence}",
        sequence=sequence,
        role=role,  # type: ignore[arg-type]
        content=content,
        run_id=f"run_{(sequence + 1) // 2}",
    )


def test_context_assembler_orders_layers_and_preserves_conversation_order() -> None:
    request = ContextAssemblyRequest(
        sections=(
            ContextSection("host", ContextLayer.HOST_CONTEXT, '{"record":"REC-1"}'),
            ContextSection("agent", ContextLayer.AGENT_INSTRUCTIONS, "Answer precisely."),
            ContextSection("platform", ContextLayer.PLATFORM, "Never expose credentials."),
        ),
        messages=(
            message(2, "assistant", "Earlier answer"),
            message(1, "user", "Earlier question"),
            message(3, "user", "Current question"),
        ),
        budget=TokenBudget(context_window_tokens=2_000, reserved_output_tokens=200),
        current_message_id="msg_3",
    )

    assembled = ContextAssembler().assemble(request)

    assert assembled.system_prompt.index('layer="platform"') < assembled.system_prompt.index(
        'layer="agent_instructions"'
    )
    assert assembled.system_prompt.index('layer="agent_instructions"') < assembled.system_prompt.index(
        'layer="host_context"'
    )
    assert [item.id for item in assembled.messages] == ["msg_1", "msg_2", "msg_3"]
    assert [entry.kind for entry in assembled.trace[-3:]] == ["message", "message", "message"]
    assert assembled.normalized_input()["compaction_id"] is None


def test_context_assembler_applies_traceable_compaction_without_mutating_sources() -> None:
    raw = (
        message(1, "user", "First question"),
        message(2, "assistant", "First answer"),
        message(3, "user", "Second question"),
    )
    compacted_sources = raw[:2]
    compaction = CompactionRecord(
        id="cmp_1",
        through_sequence=2,
        summary="The user asked a first question and received an answer.",
        source_message_ids=tuple(item.id for item in compacted_sources),
        source_digest=compaction_source_digest(compacted_sources),
        strategy="test-summary-v1",
    )

    assembled = ContextAssembler().assemble(
        ContextAssemblyRequest(
            sections=(
                ContextSection("agent", ContextLayer.AGENT_INSTRUCTIONS, "Be concise."),
            ),
            messages=raw,
            budget=TokenBudget(context_window_tokens=1_000, reserved_output_tokens=100),
            current_message_id="msg_3",
            compaction=compaction,
        )
    )

    assert [item.id for item in assembled.messages] == ["msg_3"]
    assert assembled.compaction_id == "cmp_1"
    assert "Conversation Summary" in assembled.system_prompt
    assert any(entry.kind == "compaction" and entry.id == "cmp_1" for entry in assembled.trace)
    assert [item.id for item in raw] == ["msg_1", "msg_2", "msg_3"]


def test_compaction_is_rendered_before_untrusted_host_context() -> None:
    raw = (
        message(1, "user", "First question"),
        message(2, "assistant", "First answer"),
        message(3, "user", "Current question"),
    )
    sources = raw[:2]
    compaction = CompactionRecord(
        id="cmp_order",
        through_sequence=2,
        summary="The first completed turn.",
        source_message_ids=tuple(item.id for item in sources),
        source_digest=compaction_source_digest(sources),
        strategy="test-summary-v1",
    )

    assembled = ContextAssembler().assemble(
        ContextAssemblyRequest(
            sections=(
                ContextSection(
                    "agent",
                    ContextLayer.AGENT_INSTRUCTIONS,
                    "Answer precisely.",
                ),
                ContextSection(
                    "host",
                    ContextLayer.HOST_CONTEXT,
                    '{"record":"REC-1"}',
                ),
            ),
            messages=raw,
            budget=TokenBudget(context_window_tokens=2_000, reserved_output_tokens=200),
            current_message_id="msg_3",
            compaction=compaction,
        )
    )

    assert assembled.system_prompt.index('layer="agent_instructions"') < assembled.system_prompt.index(
        'layer="compacted_conversation"'
    )
    assert assembled.system_prompt.index('layer="compacted_conversation"') < assembled.system_prompt.index(
        'layer="host_context"'
    )
    assert [entry.layer for entry in assembled.trace[:3]] == [
        "agent_instructions",
        "compacted_conversation",
        "host_context",
    ]


def test_context_assembler_rejects_tampered_or_current_covering_compaction() -> None:
    raw = (
        message(1, "user", "First question"),
        message(2, "assistant", "First answer"),
        message(3, "user", "Current question"),
    )
    tampered = CompactionRecord(
        id="cmp_bad",
        through_sequence=2,
        summary="Summary",
        source_message_ids=("msg_1", "msg_2"),
        source_digest="not-the-source-digest",
        strategy="test",
    )
    base = dict(
        sections=(ContextSection("agent", ContextLayer.AGENT_INSTRUCTIONS, "Be concise."),),
        messages=raw,
        budget=TokenBudget(context_window_tokens=1_000, reserved_output_tokens=100),
        current_message_id="msg_3",
    )

    with pytest.raises(ValueError, match="digest"):
        ContextAssembler().assemble(ContextAssemblyRequest(**base, compaction=tampered))

    covering = CompactionRecord(
        id="cmp_covering",
        through_sequence=3,
        summary="Summary",
        source_message_ids=tuple(item.id for item in raw),
        source_digest=compaction_source_digest(raw),
        strategy="test",
    )
    with pytest.raises(ValueError, match="current user message"):
        ContextAssembler().assemble(ContextAssemblyRequest(**base, compaction=covering))


def test_token_budget_marks_soft_pressure_and_fails_closed_at_hard_limit() -> None:
    estimator = HeuristicTokenEstimator(bytes_per_token=1)
    sections = (ContextSection("agent", ContextLayer.AGENT_INSTRUCTIONS, "A" * 40),)
    messages = (
        message(1, "user", "B" * 20),
        message(2, "assistant", "C" * 20),
        message(3, "user", "D" * 20),
    )

    pressured = ContextAssembler(estimator).assemble(
        ContextAssemblyRequest(
            sections=sections,
            messages=messages,
            budget=TokenBudget(
                context_window_tokens=310,
                reserved_output_tokens=100,
                compaction_trigger_ratio=0.5,
            ),
            current_message_id="msg_3",
        )
    )
    assert pressured.compaction_required is True

    with pytest.raises(ContextBudgetExceeded) as raised:
        ContextAssembler(estimator).assemble(
            ContextAssemblyRequest(
                sections=sections,
                messages=messages,
                budget=TokenBudget(
                    context_window_tokens=140,
                    reserved_output_tokens=40,
                ),
                current_message_id="msg_3",
            )
        )
    assert raised.value.estimated_tokens > raised.value.token_budget


def test_compaction_digest_binds_order_role_and_content() -> None:
    original = (message(1, "user", "Question"), message(2, "assistant", "Answer"))
    changed = (message(1, "user", "Question"), message(2, "assistant", "Changed"))

    assert compaction_source_digest(original) != compaction_source_digest(changed)


@pytest.mark.asyncio
async def test_extractive_compactor_uses_only_complete_turn_prefix_deterministically() -> None:
    raw = (
        message(1, "user", "First question with background " * 8),
        message(2, "assistant", "First answer with evidence " * 8),
        message(3, "user", "Current question must remain outside compaction"),
    )
    compactor = ExtractiveContextCompactor(
        estimator=HeuristicTokenEstimator(bytes_per_token=1)
    )
    before = tuple(raw)

    first = await compactor.compact(messages=raw, parent=None, target_tokens=150)
    second = await compactor.compact(messages=raw, parent=None, target_tokens=150)

    assert first == second
    assert first.through_sequence == 2
    assert first.source_message_ids == ("msg_1", "msg_2")
    assert first.source_digest == compaction_source_digest(raw[:2])
    assert first.strategy == "extractive-turns-v1"
    assert compactor.estimator.estimate_text(first.summary) <= 150
    assert "Current question" not in first.summary
    assert raw == before


@pytest.mark.asyncio
async def test_extractive_compactor_chains_parent_provenance_without_rewriting_raw_messages() -> None:
    raw = (
        message(1, "user", "First question"),
        message(2, "assistant", "First answer"),
        message(3, "user", "Second question"),
        message(4, "assistant", "Second answer"),
        message(5, "user", "Current question"),
    )
    compactor = ExtractiveContextCompactor()
    parent = await compactor.compact(messages=raw[:2], parent=None, target_tokens=100)

    child = await compactor.compact(messages=raw, parent=parent, target_tokens=100)

    assert child.parent_id == parent.id
    assert child.through_sequence == 4
    assert child.source_message_ids == ("msg_1", "msg_2", "msg_3", "msg_4")
    assert "Prior summary" in child.summary
    assert "Second question" in child.summary
    assert "Current question" not in child.summary


@pytest.mark.asyncio
async def test_extractive_compactor_rejects_an_incomplete_prefix() -> None:
    with pytest.raises(ValueError, match="complete conversation turn"):
        await ExtractiveContextCompactor().compact(
            messages=(message(1, "user", "Only a user message"),),
            parent=None,
            target_tokens=100,
        )
