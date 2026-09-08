from __future__ import annotations

import json

import pytest
from alcuin_api.evidence import RunCitationRegistry
from alcuin_core.tools import ToolCitation, ToolResult


def _citation(locator: str, text: str = "Observed source text") -> ToolCitation:
    return ToolCitation(
        label="Source", source="Official", locator=locator, snippet=text
    )


def _register(registry: RunCitationRegistry, *sources: ToolCitation, **kwargs):
    return registry.register_result(
        ToolResult(
            data={"hits": [{"url": source.locator} for source in sources]},
            summary="Retrieved",
            citations=sources,
        ),
        tool="web.search",
        call_id="call-1",
        **kwargs,
    )


def test_run_sources_are_stable_deduplicated_and_enter_the_model_envelope():
    registry = RunCitationRegistry("run-one")
    first = _register(registry, _citation("https://EXAMPLE.com/a#one"))
    second = _register(
        registry,
        _citation("https://example.com/a#two"),
        _citation("https://example.com/b"),
    )
    assert first.citation_events[0]["citation_id"] == "s1"
    assert len(second.citation_events) == 1
    assert second.citation_events[0]["citation_id"] == "s2"
    model = json.loads(second.model_content)
    assert [row["citation_id"] for row in model["evidence"]] == ["s1", "s2"]
    assert model["evidence"][0]["locator"] == "https://EXAMPLE.com/a#one"
    assert model["evidence"][0]["snippet"] == "Observed source text"
    assert model["data"]["hits"][0]["url"] == "https://example.com/a#two"
    assert "[[cite:sN]]" in model["evidence_notice"]


def test_registry_does_not_mix_runs_and_resumes_existing_source_numbers():
    events = [
        {
            "run_id": "run-one",
            "type": "citation.created",
            "payload": {
                "citation_id": "s7",
                "locator": "https://example.com/a",
                "snippet": "Persisted source text",
            },
        },
        {
            "run_id": "run-other",
            "type": "citation.created",
            "payload": {"citation_id": "s99", "locator": "https://private.example.com"},
        },
    ]
    registry = RunCitationRegistry("run-one", events=events)
    result = _register(
        registry, _citation("https://example.com/a"), _citation("https://example.com/b")
    )
    assert [
        row["citation_id"] for row in json.loads(result.model_content)["evidence"]
    ] == ["s7", "s8"]
    assert result.citation_events[0]["citation_id"] == "s8"
    assert "private.example.com" not in result.model_content
    fresh = _register(
        RunCitationRegistry("run-new"), _citation("https://example.com/a")
    )
    assert fresh.citation_events[0]["citation_id"] == "s1"


def test_knowledge_chunks_are_distinct_and_event_metadata_preserves_location():
    registry = RunCitationRegistry("run-one")
    result = _register(
        registry,
        *[
            ToolCitation(
                label="Manual",
                source="Knowledge",
                locator=f"knowledge://source/document#chunk-{chunk}",
                snippet=f"Text {chunk}",
                metadata={
                    "kind": "knowledge",
                    "source_id": "source",
                    "document_id": "document",
                    "chunk_index": chunk,
                    "page_number": 3,
                    "arbitrary_private_field": "must not enter evidence",
                },
            )
            for chunk in range(2)
        ],
    )
    assert len(result.citation_events) == 2
    assert result.citation_events[1]["metadata"]["chunk_index"] == 1
    assert result.citation_events[1]["metadata"]["page_number"] == 3
    assert "arbitrary_private_field" not in result.citation_events[1]["metadata"]


@pytest.mark.parametrize("limit", [1_024, 2_048, 24_000])
def test_tool_envelope_is_valid_json_and_strictly_bounded_even_with_escaped_data(limit):
    result = ToolResult(
        data={"huge": '\\"\n中' * 60_000},
        summary="A very long result" * 100,
        citations=tuple(
            _citation(f"https://example.com/{index}", 'Quoted "text". ' * 200)
            for index in range(12)
        ),
    )
    registered = RunCitationRegistry("run-one").register_result(
        result, tool="web.search", call_id="call", max_chars=limit
    )
    assert len(registered.model_content) <= limit
    parsed = json.loads(registered.model_content)
    assert parsed["data"]["truncated"] is True
    assert len(parsed["evidence"]) + parsed["omitted_evidence_count"] == 12
    events = {row["citation_id"]: row for row in registered.citation_events}
    for row in parsed["evidence"]:
        assert row["locator"] == events[row["citation_id"]]["locator"]
        assert row["snippet"] == events[row["citation_id"]]["snippet"]


def test_invalid_or_over_limit_sources_are_explicitly_omitted_without_truncated_urls():
    registered = _register(
        RunCitationRegistry("run-one"),
        _citation(""),
        _citation("https://example.com/" + "a" * 5_000),
        *[_citation(f"https://example.com/{index}") for index in range(130)],
    )
    assert len(registered.citation_events) == 128
    assert json.loads(registered.model_content)["omitted_evidence_count"] >= 4
    assert all(
        row["locator"].startswith("https://example.com/")
        for row in registered.citation_events
    )


def test_conflicting_persisted_ids_fail_closed_and_old_unidentified_sources_are_not_fabricated():
    events = [
        {
            "run_id": "run-one",
            "type": "citation.created",
            "payload": {"citation_id": "s1", "locator": url},
        }
        for url in ["https://example.com/a", "https://example.com/b"]
    ]
    with pytest.raises(ValueError, match="ambiguous"):
        RunCitationRegistry("run-one", events=events)
    legacy = [
        {
            "run_id": "run-one",
            "type": "citation.created",
            "payload": {"locator": "https://legacy.example.com"},
        }
    ]
    result = _register(
        RunCitationRegistry("run-one", events=legacy),
        _citation("https://new.example.com"),
    )
    assert result.citation_events[0]["citation_id"] == "s1"
    assert "legacy.example.com" not in result.model_content


def test_small_uncited_result_keeps_original_tool_data_without_inventing_sources():
    registered = RunCitationRegistry("run-one").register_result(
        ToolResult(data={"answer": 42}, summary="Done"),
        tool="calculator",
        call_id="call-1",
    )
    assert registered.citation_events == ()
    assert json.loads(registered.model_content)["data"] == {"answer": 42}
    assert json.loads(registered.model_content)["evidence"] == []
