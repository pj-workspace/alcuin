from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from alcuin_documents import (
    DocumentLimits,
    DocumentParseError,
    FileDocumentParser,
)
from alcuin_storage import AttachmentRepository, RepositoryConflict

from .config import Settings


IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
DOCUMENT_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class AttachmentUploadError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedAttachment:
    name: str
    media_type: str
    kind: str
    content: bytes
    sha256: str
    extracted_text: str | None
    metadata: dict[str, Any]


def public_attachment(record: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: record[key]
        for key in (
            "id",
            "workspace_id",
            "name",
            "media_type",
            "kind",
            "size_bytes",
            "status",
            "created_at",
            "expires_at",
        )
        if key in record
    }
    metadata = record.get("metadata")
    if record.get("kind") == "document" and isinstance(metadata, dict):
        document = metadata.get("document")
        if isinstance(document, dict):
            public["document"] = {
                key: document[key]
                for key in ("format", "page_count", "extracted_chars")
                if key in document
            }
    return public


class AttachmentService:
    def __init__(self, repository: AttachmentRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self.document_parser = FileDocumentParser(
            DocumentLimits(
                upload_max_bytes=settings.attachment_document_max_bytes,
                extracted_max_chars=settings.attachment_extracted_max_chars,
                pdf_max_pages=settings.attachment_pdf_max_pages,
            )
        )

    def create(
        self,
        workspace_id: str,
        *,
        upload_id: str,
        filename: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> dict[str, Any]:
        if not _UPLOAD_ID_PATTERN.fullmatch(upload_id.strip()):
            raise AttachmentUploadError(
                "invalid_upload_id",
                "upload_id must be a stable 1-160 character identifier",
            )
        prepared = self.prepare(
            filename=filename,
            declared_media_type=declared_media_type,
            content=content,
        )
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self.settings.attachment_staged_ttl_seconds)
        ).isoformat()
        try:
            record = self.repository.create_attachment(
                workspace_id,
                upload_id=upload_id.strip(),
                name=prepared.name,
                media_type=prepared.media_type,
                kind=prepared.kind,  # type: ignore[arg-type]
                content=prepared.content,
                sha256=prepared.sha256,
                expires_at=expires_at,
                extracted_text=prepared.extracted_text,
                metadata=prepared.metadata,
            )
        except RepositoryConflict as exc:
            raise AttachmentUploadError(
                "upload_id_conflict",
                str(exc),
                status_code=409,
            ) from exc
        return public_attachment(record)

    def prepare(
        self,
        *,
        filename: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> PreparedAttachment:
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not safe_name
            or len(safe_name) > 180
            or any(ord(character) < 32 for character in safe_name)
        ):
            raise AttachmentUploadError("invalid_filename", "Attachment filename is invalid")
        if not content:
            raise AttachmentUploadError("empty_attachment", "Attachment cannot be empty")
        extension = Path(safe_name).suffix.casefold()
        declared = (declared_media_type or "").partition(";")[0].strip().casefold()

        if declared in {"image/svg+xml", "image/gif"} or extension in {".svg", ".gif"}:
            raise AttachmentUploadError(
                "unsupported_media_type",
                "SVG and GIF attachments are not supported",
                status_code=415,
            )
        if extension in IMAGE_MEDIA_TYPES:
            media_type = self._validate_image(extension, declared, content)
            if len(content) > self.settings.attachment_image_max_bytes:
                raise AttachmentUploadError(
                    "attachment_too_large",
                    "Image attachment exceeds the 5 MiB limit",
                    status_code=413,
                )
            return PreparedAttachment(
                name=safe_name,
                media_type=media_type,
                kind="image",
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                extracted_text=None,
                metadata={},
            )
        if extension not in DOCUMENT_MEDIA_TYPES:
            raise AttachmentUploadError(
                "unsupported_media_type",
                "Attachment type is not supported",
                status_code=415,
            )
        expected_media_type = DOCUMENT_MEDIA_TYPES[extension]
        accepted_declared = {expected_media_type, "application/octet-stream"}
        if extension in {".md", ".markdown"}:
            accepted_declared.add("text/plain")
        if declared and declared not in accepted_declared:
            raise AttachmentUploadError(
                "media_type_mismatch",
                "Declared media type does not match the document",
                status_code=415,
            )
        if len(content) > self.settings.attachment_document_max_bytes:
            raise AttachmentUploadError(
                "attachment_too_large",
                "Document attachment exceeds the 8 MiB limit",
                status_code=413,
            )
        try:
            parsed = self.document_parser.parse(
                filename=safe_name,
                content_type=expected_media_type,
                data=content,
            )
        except DocumentParseError as exc:
            raise AttachmentUploadError(
                getattr(exc, "code", "invalid_document"),
                str(exc),
                status_code=422,
            ) from exc
        document_format = {
            ".txt": "txt",
            ".md": "markdown",
            ".markdown": "markdown",
            ".pdf": "pdf",
            ".docx": "docx",
        }[extension]
        document_metadata: dict[str, Any] = {
            "format": document_format,
            "extracted_chars": len(parsed.content),
        }
        if isinstance(parsed.metadata.get("page_count"), int):
            document_metadata["page_count"] = int(parsed.metadata["page_count"])
        return PreparedAttachment(
            name=safe_name,
            media_type=expected_media_type,
            kind="document",
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            extracted_text=parsed.content,
            metadata={"document": document_metadata},
        )

    @staticmethod
    def _validate_image(extension: str, declared: str, content: bytes) -> str:
        expected = IMAGE_MEDIA_TYPES[extension]
        if declared and declared not in {expected, "application/octet-stream"}:
            raise AttachmentUploadError(
                "media_type_mismatch",
                "Declared media type does not match the image",
                status_code=415,
            )
        detected = None
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            detected = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            detected = "image/jpeg"
        elif (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        ):
            detected = "image/webp"
        if detected != expected:
            raise AttachmentUploadError(
                "invalid_image",
                "Image signature does not match its filename",
                status_code=422,
            )
        return expected
