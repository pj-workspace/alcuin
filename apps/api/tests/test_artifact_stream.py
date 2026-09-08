from __future__ import annotations

import json

import pytest
from alcuin_api.artifact_stream import ArtifactStream, final_answer_committed
from alcuin_core.contracts import EventType


def _decode(text: str, *, chunk_size: int = 7):
    decoder = ArtifactStream("run-artifacts", clock=lambda: 1.0)
    output = []
    for offset in range(0, len(text), chunk_size):
        output.extend(decoder.feed(text[offset : offset + chunk_size]))
    output.extend(decoder.finish())
    answer = "".join(
        payload["delta"] for kind, payload in output if kind == EventType.MESSAGE_DELTA
    )
    artifacts = [
        payload for kind, payload in output if kind == EventType.ARTIFACT_UPDATED
    ]
    finals = [payload["artifact"] for payload in artifacts if not payload["streaming"]]
    return answer, finals, artifacts


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 11, 29, 1_000])
def test_arbitrary_boundaries_keep_two_deliverables_separate_from_the_answer(
    chunk_size,
):
    text = '<alcuin-answer>Created both files.\n<alcuin-artifact title="Report" content-type="text/markdown"># Report\n\nEvidence [[cite:s1]].</alcuin-artifact>\n<alcuin-artifact title="Dashboard" content-type="text/html"><html><body><button>View</button></body></html></alcuin-artifact>\nYou can edit them.</alcuin-answer>'
    answer, finals, _ = _decode(text, chunk_size=chunk_size)
    assert answer == "Created both files.\n\n\nYou can edit them."
    assert [item["content_type"] for item in finals] == ["text/markdown", "text/html"]
    assert finals[0]["content"] == "# Report\n\nEvidence [[cite:s1]]."
    assert finals[1]["content"] == "<html><body><button>View</button></body></html>"
    assert [item["id"] for item in finals] == [
        "artifact-run-artifacts-1",
        "artifact-run-artifacts-2",
    ]


@pytest.mark.parametrize("chunk_size", [1, 4, 99])
def test_code_literals_never_commit_or_create_artifacts(chunk_size):
    literal = 'Literal `<alcuin-answer>` and `<alcuin-artifact title="Example">` stay visible.'
    assert _decode(literal, chunk_size=chunk_size)[:2] == (literal, [])
    body = '\n```xml\n<alcuin-artifact title="Example">not a file</alcuin-artifact>\n```\nAnd `<alcuin-artifact title="Inline">` is code.\n'
    assert _decode(f"<alcuin-answer>{body}</alcuin-answer>", chunk_size=chunk_size)[
        :2
    ] == (body, [])


def test_markdown_artifact_can_document_a_literal_closing_tag_in_a_code_fence():
    content = "# Protocol\n```xml\n</alcuin-artifact>\n```\nEnd."
    _, finals, _ = _decode(
        f'<alcuin-answer><alcuin-artifact title="Protocol">{content}</alcuin-artifact></alcuin-answer>',
        chunk_size=1,
    )
    assert finals[0]["content"] == content


def test_json_is_emitted_only_when_complete_and_invalid_json_fails():
    valid = '<alcuin-answer><alcuin-artifact title="Data" content-type="application/json">{"answer":42}</alcuin-artifact></alcuin-answer>'
    _, finals, events = _decode(valid, chunk_size=1)
    assert len(events) == 1
    assert json.loads(finals[0]["content"]) == {"answer": 42}
    with pytest.raises(ValueError, match="JSON artifact is invalid"):
        _decode(valid.replace('{"answer":42}', '{"answer":}'))


def test_incomplete_answer_and_artifact_fail_while_prior_partial_output_remains_available():
    decoder = ArtifactStream("run-partial")
    output = decoder.feed("<alcuin-answer>This answer was partially streamed.")
    assert output == [
        (EventType.MESSAGE_DELTA, {"delta": "This answer was partially streamed."})
    ]
    with pytest.raises(ValueError, match="answer was interrupted"):
        decoder.finish()
    decoder = ArtifactStream("run-partial")
    output = decoder.feed(
        '<alcuin-answer><alcuin-artifact title="Partial"># Saved partial'
    )
    assert output[0][1]["artifact"]["content"] == "# Saved partial"
    assert output[0][1]["streaming"] is True
    with pytest.raises(ValueError, match="artifact was interrupted"):
        decoder.finish()


def test_unsupported_oversized_and_incomplete_headers_are_rejected():
    with pytest.raises(ValueError, match="content type is unsupported"):
        _decode(
            '<alcuin-answer><alcuin-artifact title="File" content-type="application/x-executable">no</alcuin-artifact></alcuin-answer>'
        )
    with pytest.raises(ValueError, match="incomplete header"):
        _decode('<alcuin-answer><alcuin-artifact title="File"')
    with pytest.raises(ValueError, match="safe size limit"):
        _decode(
            '<alcuin-answer><alcuin-artifact title="' + "a" * 1_025, chunk_size=1_500
        )
    with pytest.raises(ValueError, match="500000"):
        _decode(
            '<alcuin-answer><alcuin-artifact title="File">'
            + "a" * 500_001
            + "</alcuin-artifact></alcuin-answer>",
            chunk_size=600_000,
        )


def test_title_entities_and_greater_than_inside_quoted_attributes_are_supported():
    _, finals, _ = _decode(
        '<alcuin-answer><alcuin-artifact title="A > B &amp; C" content-type="text/plain">Text</alcuin-artifact></alcuin-answer>',
        chunk_size=1,
    )
    assert finals[0]["title"] == "A > B & C"


def test_artifact_and_total_output_limits_remain_bounded():
    block = '<alcuin-artifact title="File">x</alcuin-artifact>'
    with pytest.raises(ValueError, match="at most 8"):
        _decode("<alcuin-answer>" + block * 9 + "</alcuin-answer>")
    decoder = ArtifactStream("run-big")
    with pytest.raises(ValueError, match="safe size limit"):
        decoder.feed("a" * 2_000_001)


def test_normal_non_protocol_output_remains_a_conversation_and_commit_requires_prefix():
    content = 'Ordinary reply with <alcuin-artifact title="not a file">literal tag</alcuin-artifact>.'
    assert _decode(content)[:2] == (content, [])
    assert final_answer_committed(" \n<alcuin-answer>Final")
    assert not final_answer_committed("Progress first <alcuin-answer>Final")
    assert not final_answer_committed("`<alcuin-answer>` is literal")
