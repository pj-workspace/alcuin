"""Deterministic in-memory Attachment port used by service and contract tests."""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from alcuin_core.contracts import utc_now

from .errors import AttachmentBindingError, RepositoryConflict
from .repository import new_id


class InMemoryAttachmentRepository:
    """Attachment lifecycle equivalent to the PostgreSQL adapter, without Run storage."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.attachments: dict[tuple[str, str], dict[str, Any]] = {}
        self.uploads: dict[tuple[str, str], str] = {}
        self.blobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.bindings: dict[tuple[str, str], tuple[str, int]] = {}

    def create_attachment(
        self,
        workspace_id: str,
        *,
        upload_id: str,
        name: str,
        media_type: str,
        kind: str,
        content: bytes,
        sha256: str,
        expires_at: str,
        extracted_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ValueError("attachment sha256 does not match its content")
        with self.lock:
            upload_key = (workspace_id, upload_id)
            existing_id = self.uploads.get(upload_key)
            if existing_id:
                existing = self.attachments[(workspace_id, existing_id)]
                if (
                    existing["sha256"] != sha256
                    or existing["name"] != name
                    or existing["media_type"] != media_type
                ):
                    raise RepositoryConflict(
                        "upload_id is already bound to different attachment content"
                    )
                return self._public(workspace_id, existing_id)
            attachment_id = new_id("att")
            record = {
                "id": attachment_id,
                "workspace_id": workspace_id,
                "upload_id": upload_id,
                "name": name,
                "media_type": media_type,
                "kind": kind,
                "size_bytes": len(content),
                "sha256": sha256,
                "status": "ready",
                "metadata": dict(metadata or {}),
                "created_at": utc_now(),
                "expires_at": expires_at,
            }
            self.attachments[(workspace_id, attachment_id)] = record
            self.uploads[upload_key] = attachment_id
            self.blobs[(workspace_id, attachment_id)] = {
                **record,
                "content": bytes(content),
                "extracted_text": extracted_text,
            }
            return self._public(workspace_id, attachment_id)

    def _public(self, workspace_id: str, attachment_id: str) -> dict[str, Any]:
        record = dict(self.attachments[(workspace_id, attachment_id)])
        record["bound"] = (workspace_id, attachment_id) in self.bindings
        return record

    def get_attachment(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        with self.lock:
            if (workspace_id, attachment_id) not in self.attachments:
                return None
            return self._public(workspace_id, attachment_id)

    def get_attachment_blob(
        self,
        workspace_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        with self.lock:
            blob = self.blobs.get((workspace_id, attachment_id))
            return dict(blob) if blob else None

    def list_message_attachments(
        self,
        workspace_id: str,
        message_id: str,
    ) -> list[dict[str, Any]]:
        with self.lock:
            rows = [
                {
                    **self._public(workspace_id, attachment_id),
                    "message_id": bound_message,
                    "position": position,
                }
                for (bound_workspace, attachment_id), (bound_message, position) in self.bindings.items()
                if bound_workspace == workspace_id and bound_message == message_id
            ]
        return sorted(rows, key=lambda row: int(row["position"]))

    def delete_attachment(self, workspace_id: str, attachment_id: str) -> bool:
        with self.lock:
            key = (workspace_id, attachment_id)
            record = self.attachments.get(key)
            if record is None:
                return False
            if key in self.bindings:
                raise RepositoryConflict("Bound attachments cannot be deleted")
            self.attachments.pop(key)
            self.blobs.pop(key, None)
            self.uploads.pop((workspace_id, str(record["upload_id"])), None)
            return True

    def bind_attachments(
        self,
        workspace_id: str,
        message_id: str,
        attachment_ids: tuple[str, ...],
        *,
        max_total_attachment_bytes: int,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically validate and bind staged resources for in-memory service tests."""
        accepted_at = now or utc_now()
        with self.lock:
            records = [
                self.attachments.get((workspace_id, attachment_id))
                for attachment_id in attachment_ids
            ]
            if any(record is None for record in records):
                raise AttachmentBindingError(
                    "attachment_unavailable",
                    "One or more attachments are unavailable in this Workspace",
                )
            concrete = [record for record in records if record is not None]
            if any(
                (workspace_id, str(record["id"])) in self.bindings
                for record in concrete
            ):
                raise AttachmentBindingError(
                    "attachment_already_bound",
                    "An attachment can only be bound to one user message",
                )
            if any(str(record["expires_at"]) <= accepted_at for record in concrete):
                raise AttachmentBindingError(
                    "attachment_expired",
                    "One or more staged attachments have expired",
                )
            if (
                sum(int(record["size_bytes"]) for record in concrete)
                > max_total_attachment_bytes
            ):
                raise AttachmentBindingError(
                    "attachment_total_too_large",
                    "Run attachments exceed the total size limit",
                )
            for position, record in enumerate(concrete):
                self.bindings[(workspace_id, str(record["id"]))] = (
                    message_id,
                    position,
                )
            return [self._public(workspace_id, str(record["id"])) for record in concrete]
