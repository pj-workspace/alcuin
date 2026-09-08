from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest

from alcuin_storage import (
    AttachmentBindingError,
    InMemoryAttachmentRepository,
    PostgresStore,
    RepositoryConflict,
)


DATABASE_URL = os.environ.get("ALCUIN_TEST_POSTGRES_URL")


def require_test_database(url: str | None) -> str | None:
    if url is None:
        return None
    database_name = urlsplit(url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to reset a PostgreSQL database whose name does not end in '_test'"
        )
    return url


DATABASE_URL = require_test_database(DATABASE_URL)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="ALCUIN_TEST_POSTGRES_URL is required for PostgreSQL attachment tests",
)


def reset_database() -> None:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            """TRUNCATE TABLE
            artifact_versions, artifacts,
            message_attachments, attachment_blobs, attachments,
            run_context_assemblies, thread_compactions, messages,
            knowledge_documents, knowledge_sources, extensions, approvals, events,
            runs, threads, agent_versions, agents, workspaces
            CASCADE"""
        )


@pytest.fixture
def store() -> PostgresStore:
    assert DATABASE_URL is not None
    reset_database()
    repository = PostgresStore(DATABASE_URL, pool_max_size=4)
    try:
        yield repository
    finally:
        repository.close()


def future_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def create_attachment(
    repository: Any,
    *,
    workspace_id: str = "ws_demo",
    upload_id: str,
    content: bytes,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return repository.create_attachment(
        workspace_id,
        upload_id=upload_id,
        name=f"{upload_id}.txt",
        media_type="text/plain",
        kind="document",
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        expires_at=expires_at or future_expiry(),
        extracted_text=content.decode("utf-8"),
        metadata={
            "document": {
                "format": "txt",
                "extracted_chars": len(content.decode("utf-8")),
            }
        },
    )


def attachment_part(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "attachment",
        "attachment_id": record["id"],
        "name": record["name"],
        "media_type": record["media_type"],
        "kind": record["kind"],
        "size_bytes": record["size_bytes"],
        "sha256": record["sha256"],
    }


def test_postgres_attachment_is_idempotent_workspace_scoped_and_reloadable(
    store: PostgresStore,
) -> None:
    content = b"workspace-private attachment"
    created = create_attachment(
        store,
        upload_id="upload-reload",
        content=content,
    )
    repeated = create_attachment(
        store,
        upload_id="upload-reload",
        content=content,
    )

    assert repeated["id"] == created["id"]
    assert store.get_attachment("ws_other", created["id"]) is None
    assert store.get_attachment_blob("ws_other", created["id"]) is None
    with pytest.raises(RepositoryConflict, match="different attachment content"):
        create_attachment(
            store,
            upload_id="upload-reload",
            content=b"different",
        )

    assert DATABASE_URL is not None
    restarted = PostgresStore(DATABASE_URL, pool_max_size=2)
    try:
        reloaded = restarted.get_attachment_blob("ws_demo", created["id"])
        assert reloaded is not None
        assert reloaded["content"] == content
        assert reloaded["extracted_text"] == content.decode("utf-8")
        assert reloaded["sha256"] == hashlib.sha256(content).hexdigest()
    finally:
        restarted.close()


def test_postgres_run_binds_attachments_atomically_and_allows_failure_retry(
    store: PostgresStore,
) -> None:
    valid = create_attachment(
        store,
        upload_id="upload-valid",
        content=b"valid text",
    )
    expired = create_attachment(
        store,
        upload_id="upload-expired",
        content=b"expired text",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    agent = store.list_agents("ws_demo")[0]
    thread = store.create_thread("ws_demo", agent["id"], "Atomic attachment", {})
    parts = [
        {"type": "text", "text": "Use both documents"},
        attachment_part(valid),
        attachment_part(expired),
    ]

    with pytest.raises(AttachmentBindingError) as failed:
        store.create_run_with_messages(
            "ws_demo",
            thread["id"],
            agent["current_version_id"],
            "Use both documents",
            parts,
            10,
            attachment_ids=(valid["id"], expired["id"]),
        )
    assert failed.value.code == "attachment_expired"
    assert store.list_runs("ws_demo") == []
    assert store.list_messages("ws_demo", thread["id"]) == []
    assert store.get_attachment("ws_demo", valid["id"])["bound"] is False

    run = store.create_run_with_messages(
        "ws_demo",
        thread["id"],
        agent["current_version_id"],
        "Use the valid document",
        [
            {"type": "text", "text": "Use the valid document"},
            attachment_part(valid),
        ],
        8,
        attachment_ids=(valid["id"],),
    )
    assert store.get_attachment("ws_demo", valid["id"])["bound"] is True
    message = store.get_message("ws_demo", run["input_message_id"])
    assert message is not None
    serialized = json.dumps(message, ensure_ascii=False, default=str)
    assert "data:" not in serialized.casefold()
    assert "storage_key" not in serialized.casefold()
    assert store.list_message_attachments(
        "ws_demo", run["input_message_id"]
    )[0]["id"] == valid["id"]

    store.set_run_status("ws_demo", run["id"], "completed")
    another = store.create_thread("ws_demo", agent["id"], "Reuse rejected", {})
    with pytest.raises(AttachmentBindingError) as reused:
        store.create_run_with_messages(
            "ws_demo",
            another["id"],
            agent["current_version_id"],
            "Reuse",
            [
                {"type": "text", "text": "Reuse"},
                attachment_part(valid),
            ],
            3,
            attachment_ids=(valid["id"],),
        )
    assert reused.value.code == "attachment_already_bound"
    assert store.list_messages("ws_demo", another["id"]) == []


def test_in_memory_attachment_binding_matches_atomic_contract() -> None:
    repository = InMemoryAttachmentRepository()
    valid = create_attachment(
        repository,
        upload_id="memory-valid",
        content=b"valid",
    )
    expired = create_attachment(
        repository,
        upload_id="memory-expired",
        content=b"expired",
        expires_at="2000-01-01T00:00:00+00:00",
    )

    with pytest.raises(AttachmentBindingError) as failed:
        repository.bind_attachments(
            "ws_demo",
            "msg_failed",
            (valid["id"], expired["id"]),
            max_total_attachment_bytes=20 * 1024 * 1024,
        )
    assert failed.value.code == "attachment_expired"
    assert repository.get_attachment("ws_demo", valid["id"])["bound"] is False

    bound = repository.bind_attachments(
        "ws_demo",
        "msg_retry",
        (valid["id"],),
        max_total_attachment_bytes=20 * 1024 * 1024,
    )
    assert bound[0]["bound"] is True
    with pytest.raises(AttachmentBindingError) as reused:
        repository.bind_attachments(
            "ws_demo",
            "msg_reuse",
            (valid["id"],),
            max_total_attachment_bytes=20 * 1024 * 1024,
        )
    assert reused.value.code == "attachment_already_bound"
