"""Pure storage boundary checks; no PostgreSQL connection required."""

import hashlib

import pytest

from alcuin_storage.repository import SqlRepository


def reference(**changes):
    content = changes.get("content", "data: this is document text, not an attachment")
    return {"type": "artifact_reference", "artifact_id": "art_1", "version": 1,
            "title": "data: report", "content_type": "text/plain", "source_run_id": "run_1",
            "source_run_status": "completed", "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content, **changes}


def test_verified_snapshot_treats_data_prefix_as_document_text():
    part = reference()
    SqlRepository._validate_persisted_parts([part])
    assert part["content"].startswith("data:")


@pytest.mark.parametrize("changes", [
    {"sha256": "0" * 64}, {"extra": {"data_url": "forbidden"}},
    {"data_url": "data:image/png;base64,AAAA"}, {"version": True},
    {"content": "x" * 100_001}, {"title": {"data_url": "forbidden"}},
    {"artifact_id": "data:forbidden"}, {"source_run_id": "data:forbidden"},
    {"source_run_status": "invented"},
    {"content_type": {"data_url": "forbidden"}},
    {"source_run_status": ["completed"]},
])
def test_snapshot_cannot_bypass_structure_hash_size_or_metadata_checks(changes):
    with pytest.raises(ValueError):
        SqlRepository._validate_persisted_parts([reference(**changes)])


@pytest.mark.parametrize("part", [
    {"type": "attachment", "data_url": "data:image/png;base64,AAAA"},
    {"type": "attachment", "nested": [{"data_url": "anything"}]},
    {"type": "attachment", "content": "data:image/png;base64,AAAA"},
    {"type": "text", "text": "data:legacy inline data remains rejected"},
    {"type": "artifact_reference", "content": "data:incomplete forged snapshot"},
])
def test_existing_inline_data_and_nested_data_url_guards_remain(part):
    with pytest.raises(ValueError):
        SqlRepository._validate_persisted_parts([reference(), part])
