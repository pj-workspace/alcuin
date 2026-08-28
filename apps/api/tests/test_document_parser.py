from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from alcuin_api.config import Settings
from alcuin_core.contracts import KnowledgeSourceCreate
from alcuin_api.document_parser import (
    DocumentParseError,
    DocumentTooLargeError,
    FileDocumentParser,
    UnsupportedDocumentError,
)
from alcuin_api.knowledge import KnowledgeService
from alcuin_api.main import create_app
from support import create_test_store


class CapturingIndex:
    index_revision = "parser-test-v1"

    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    async def upsert_document(self, **kwargs: Any) -> None:
        self.upserts.append(kwargs)

    async def search(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []

    async def delete_source(self, *, workspace_id: str, source_id: str) -> None:
        del workspace_id, source_id

    async def health(self) -> dict[str, Any]:
        return {"status": "healthy"}

    async def aclose(self) -> None:
        return None


def parser() -> FileDocumentParser:
    return FileDocumentParser(
        Settings(
            knowledge_upload_max_bytes=64 * 1024,
            knowledge_extracted_max_chars=10_000,
            knowledge_pdf_max_pages=10,
        )
    )


def make_docx(*paragraphs: str) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


def make_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_parser_extracts_text_markdown_pdf_and_docx() -> None:
    document_parser = parser()

    text = document_parser.parse(
        filename="..\\private/operations.txt",
        content_type="text/plain",
        data="运行手册：写操作必须审批。".encode(),
    )
    markdown = document_parser.parse(
        filename="platform.md",
        content_type="text/markdown",
        data=b"# Alcuin\n\nExtension manifests are versioned.",
        title="Platform guide",
    )
    pdf = document_parser.parse(
        filename="embedding.pdf",
        content_type="application/pdf",
        data=make_pdf("Alcuin PDF knowledge retrieval"),
    )
    docx = document_parser.parse(
        filename="extensions.docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        data=make_docx("MCP supports stdio.", "OpenAPI supports governed imports."),
    )

    assert text.title == "operations"
    assert text.source_uri == "upload://operations.txt"
    assert text.metadata["filename"] == "operations.txt"
    assert markdown.title == "Platform guide"
    assert markdown.metadata["document_kind"] == "markdown"
    assert "PDF knowledge retrieval" in pdf.content
    assert pdf.metadata["page_count"] == 1
    assert docx.content == "MCP supports stdio.\n\nOpenAPI supports governed imports."


def test_parser_rejects_unsupported_invalid_and_oversized_files() -> None:
    document_parser = parser()

    with pytest.raises(UnsupportedDocumentError, match="not supported"):
        document_parser.parse(
            filename="payload.exe",
            content_type="application/octet-stream",
            data=b"MZ payload",
        )
    with pytest.raises(DocumentParseError, match="PDF signature"):
        document_parser.parse(
            filename="fake.pdf",
            content_type="application/pdf",
            data=b"not a PDF",
        )
    with pytest.raises(DocumentParseError, match="UTF-8"):
        document_parser.parse(
            filename="legacy.txt",
            content_type="text/plain",
            data=b"\xff\xfe\x00",
        )
    with pytest.raises(DocumentTooLargeError, match="size limit"):
        document_parser.parse(
            filename="large.md",
            content_type="text/markdown",
            data=b"a" * (64 * 1024 + 1),
        )
    with pytest.raises(DocumentParseError, match="declarations"):
        document_parser.parse(
            filename="entity.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=make_docx("<!DOCTYPE w:document [<!ENTITY x 'unsafe'>]>")
            .replace(b"&lt;", b"<")
            .replace(b"&gt;", b">")
            .replace(b"&apos;", b"'"),
        )


def test_file_upload_api_is_scoped_idempotent_and_does_not_echo_content() -> None:
    store = create_test_store()
    index = CapturingIndex()
    service = KnowledgeService(store, index)
    app = create_app(
        Settings(
            searxng_url="",
            qdrant_url="",
            dashscope_api_key="",
            knowledge_upload_max_bytes=64 * 1024,
            knowledge_extracted_max_chars=10_000,
        ),
        store=store,
        knowledge_service=service,
    )
    source = store.create_knowledge_source(
        "ws_demo",
        KnowledgeSourceCreate(name="Uploaded handbook"),
    )
    headers = {"X-Alcuin-Workspace": "ws_demo"}
    content = b"# Extension guide\n\nMCP and OpenAPI are governed entrypoints."

    with TestClient(app) as client:
        first = client.post(
            f"/v1/knowledge/sources/{source['id']}/files",
            headers=headers,
            files={"file": ("guide.md", content, "text/markdown")},
            data={"title": "Uploaded extension guide"},
        )
        duplicate = client.post(
            f"/v1/knowledge/sources/{source['id']}/files",
            headers=headers,
            files={"file": ("guide.md", content, "text/markdown")},
        )
        unsupported = client.post(
            f"/v1/knowledge/sources/{source['id']}/files",
            headers=headers,
            files={"file": ("guide.exe", b"binary", "application/octet-stream")},
        )
        foreign = client.post(
            f"/v1/knowledge/sources/{source['id']}/files",
            headers={"X-Alcuin-Workspace": "ws_missing"},
            files={"file": ("guide.md", content, "text/markdown")},
        )

    assert first.status_code == 201
    assert first.json()["indexed"] is True
    assert first.json()["parsed"] == {
        "filename": "guide.md",
        "kind": "markdown",
        "characters": len(content.decode()),
    }
    assert "MCP and OpenAPI" not in first.text
    assert duplicate.status_code == 200
    assert duplicate.json()["indexed"] is False
    assert len(index.upserts) == 1
    assert index.upserts[0]["metadata"]["filename"] == "guide.md"
    assert unsupported.status_code == 415
    assert foreign.status_code == 404
