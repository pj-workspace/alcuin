from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from xml.etree import ElementTree

from pypdf import PdfReader


@dataclass(frozen=True)
class DocumentLimits:
    """Caller-owned resource budgets for one parsed document.

    ``docx_max_uncompressed_bytes`` is optional to preserve the historical Alcuin policy: four
    times the upload limit, with a 16 MiB floor and a 64 MiB ceiling. Conversation and knowledge
    services may instead provide an explicit expansion budget.
    """

    upload_max_bytes: int = 8 * 1024 * 1024
    extracted_max_chars: int = 2_000_000
    pdf_max_pages: int = 500
    docx_max_uncompressed_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.upload_max_bytes < 1:
            raise ValueError("upload_max_bytes must be positive")
        if self.extracted_max_chars < 1:
            raise ValueError("extracted_max_chars must be positive")
        if self.pdf_max_pages < 1:
            raise ValueError("pdf_max_pages must be positive")
        if (
            self.docx_max_uncompressed_bytes is not None
            and self.docx_max_uncompressed_bytes < 1
        ):
            raise ValueError("docx_max_uncompressed_bytes must be positive")

    @property
    def effective_docx_max_uncompressed_bytes(self) -> int:
        if self.docx_max_uncompressed_bytes is not None:
            return self.docx_max_uncompressed_bytes
        return min(
            max(self.upload_max_bytes * 4, 16 * 1024 * 1024),
            64 * 1024 * 1024,
        )


class DocumentParseError(ValueError):
    """A stable document parsing failure safe to expose to API clients."""

    code = "invalid_document"


class DocumentTooLargeError(DocumentParseError):
    code = "document_too_large"


class UnsupportedDocumentError(DocumentParseError):
    code = "unsupported_document"


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    content: str
    source_uri: str
    metadata: dict[str, Any]


class DocumentParser(Protocol):
    supported_extensions: tuple[str, ...]

    def parse(
        self,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
        title: str | None = None,
    ) -> ParsedDocument: ...


class FileDocumentParser:
    """Bounded parser for common document files; never executes embedded content."""

    supported_extensions = (".txt", ".md", ".markdown", ".pdf", ".docx")
    _word_namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def __init__(self, limits: DocumentLimits) -> None:
        self.max_upload_bytes = limits.upload_max_bytes
        self.max_chars = limits.extracted_max_chars
        self.max_pdf_pages = limits.pdf_max_pages
        self.max_docx_uncompressed_bytes = (
            limits.effective_docx_max_uncompressed_bytes
        )

    def parse(
        self,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
        title: str | None = None,
    ) -> ParsedDocument:
        safe_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not safe_name
            or len(safe_name) > 240
            or any(ord(character) < 32 for character in safe_name)
        ):
            raise DocumentParseError("Document filename is invalid")
        if len(data) > self.max_upload_bytes:
            raise DocumentTooLargeError("Document exceeds the upload size limit")
        if not data:
            raise DocumentParseError("Document is empty")
        extension = Path(safe_name).suffix.lower()
        if extension not in self.supported_extensions:
            raise UnsupportedDocumentError("Document type is not supported")

        metadata: dict[str, Any] = {
            "imported_via": "file-upload",
            "filename": safe_name,
            "file_extension": extension,
            "file_size": len(data),
        }
        if content_type:
            metadata["declared_content_type"] = content_type[:120]

        if extension in {".txt", ".md", ".markdown"}:
            content = self._parse_text(data)
            metadata["document_kind"] = "markdown" if extension != ".txt" else "text"
        elif extension == ".pdf":
            content, page_count = self._parse_pdf(data)
            metadata.update(document_kind="pdf", page_count=page_count)
        else:
            content = self._parse_docx(data)
            metadata["document_kind"] = "docx"

        content = content.strip()
        if not content:
            raise DocumentParseError("Document contains no extractable text")
        if len(content) > self.max_chars:
            raise DocumentTooLargeError("Extracted document text exceeds the limit")

        resolved_title = (title or Path(safe_name).stem).strip()
        resolved_title = re.sub(r"\s+", " ", resolved_title)
        if not resolved_title:
            resolved_title = "Untitled document"
        if len(resolved_title) > 200:
            raise DocumentParseError("Document title is too long")
        return ParsedDocument(
            title=resolved_title,
            content=content,
            source_uri=f"upload://{quote(safe_name)}",
            metadata=metadata,
        )

    def _parse_text(self, data: bytes) -> str:
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentParseError("Text documents must use UTF-8 encoding") from exc
        if "\x00" in content:
            raise DocumentParseError("Text document contains binary data")
        return content

    def _parse_pdf(self, data: bytes) -> tuple[str, int]:
        if not data.startswith(b"%PDF-"):
            raise DocumentParseError("PDF signature is invalid")
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise DocumentParseError("Encrypted PDFs are not supported")
            page_count = len(reader.pages)
            if page_count > self.max_pdf_pages:
                raise DocumentTooLargeError("PDF exceeds the page limit")
            parts: list[str] = []
            characters = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                characters += len(text)
                if characters > self.max_chars:
                    raise DocumentTooLargeError(
                        "Extracted document text exceeds the limit"
                    )
                if text.strip():
                    parts.append(text.strip())
            return "\n\n".join(parts), page_count
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError("PDF could not be parsed") from exc

    def _parse_docx(self, data: bytes) -> str:
        if not data.startswith(b"PK"):
            raise DocumentParseError("DOCX signature is invalid")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if sum(member.file_size for member in members) > self.max_docx_uncompressed_bytes:
                    raise DocumentTooLargeError("DOCX expanded content exceeds the limit")
                try:
                    document_info = archive.getinfo("word/document.xml")
                except KeyError as exc:
                    raise DocumentParseError("DOCX document body is missing") from exc
                if document_info.file_size > self.max_docx_uncompressed_bytes:
                    raise DocumentTooLargeError("DOCX document body exceeds the limit")
                xml = archive.read(document_info)
        except DocumentParseError:
            raise
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise DocumentParseError("DOCX could not be parsed") from exc

        try:
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise DocumentParseError("DOCX document XML declarations are not allowed")
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise DocumentParseError("DOCX document XML is invalid") from exc

        paragraph_tag = self._word_namespace + "p"
        text_tag = self._word_namespace + "t"
        tab_tag = self._word_namespace + "tab"
        break_tags = {
            self._word_namespace + "br",
            self._word_namespace + "cr",
        }
        paragraphs: list[str] = []
        characters = 0
        for paragraph in root.iter(paragraph_tag):
            pieces: list[str] = []
            for node in paragraph.iter():
                if node.tag == text_tag and node.text:
                    pieces.append(node.text)
                elif node.tag == tab_tag:
                    pieces.append("\t")
                elif node.tag in break_tags:
                    pieces.append("\n")
            text = "".join(pieces).strip()
            if text:
                characters += len(text)
                if characters > self.max_chars:
                    raise DocumentTooLargeError(
                        "Extracted document text exceeds the limit"
                    )
                paragraphs.append(text)
        return "\n\n".join(paragraphs)
