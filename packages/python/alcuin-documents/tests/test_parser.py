from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from alcuin_documents import (
    DocumentLimits,
    DocumentParseError,
    DocumentTooLargeError,
    FileDocumentParser,
    UnsupportedDocumentError,
)


def parser(**overrides: int) -> FileDocumentParser:
    values = {
        "upload_max_bytes": 64 * 1024,
        "extracted_max_chars": 10_000,
        "pdf_max_pages": 10,
        **overrides,
    }
    return FileDocumentParser(DocumentLimits(**values))


def make_docx(*paragraphs: str, extra_files: dict[str, bytes] | None = None) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
        for path, content in (extra_files or {}).items():
            archive.writestr(path, content)
    return output.getvalue()


def make_pdf(*pages: str, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for text in pages:
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
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_extracts_all_supported_document_formats() -> None:
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
        title="  Platform   guide  ",
    )
    pdf = document_parser.parse(
        filename="embedding.pdf",
        content_type="application/pdf",
        data=make_pdf("Alcuin PDF document parsing"),
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
    assert text.metadata["document_kind"] == "text"
    assert markdown.title == "Platform guide"
    assert markdown.metadata["document_kind"] == "markdown"
    assert "PDF document parsing" in pdf.content
    assert pdf.metadata["page_count"] == 1
    assert docx.content == "MCP supports stdio.\n\nOpenAPI supports governed imports."


def test_limits_are_caller_configurable_without_changing_knowledge_defaults() -> None:
    defaults = DocumentLimits()
    conversation = DocumentLimits(
        upload_max_bytes=8 * 1024 * 1024,
        extracted_max_chars=120_000,
        pdf_max_pages=100,
        docx_max_uncompressed_bytes=24 * 1024 * 1024,
    )

    assert defaults.effective_docx_max_uncompressed_bytes == 32 * 1024 * 1024
    assert conversation.extracted_max_chars == 120_000
    assert conversation.pdf_max_pages == 100
    assert conversation.effective_docx_max_uncompressed_bytes == 24 * 1024 * 1024
    for field in (
        "upload_max_bytes",
        "extracted_max_chars",
        "pdf_max_pages",
        "docx_max_uncompressed_bytes",
    ):
        with pytest.raises(ValueError, match="positive"):
            DocumentLimits(**{field: 0})


def test_rejects_unsupported_empty_binary_and_oversized_text_files() -> None:
    document_parser = parser()

    with pytest.raises(UnsupportedDocumentError, match="not supported"):
        document_parser.parse(
            filename="payload.exe",
            content_type="application/octet-stream",
            data=b"MZ payload",
        )
    with pytest.raises(DocumentParseError, match="empty"):
        document_parser.parse(filename="empty.txt", content_type="text/plain", data=b"")
    with pytest.raises(DocumentParseError, match="UTF-8"):
        document_parser.parse(
            filename="legacy.txt", content_type="text/plain", data=b"\xff\xfe"
        )
    with pytest.raises(DocumentParseError, match="binary"):
        document_parser.parse(
            filename="binary.md", content_type="text/markdown", data=b"a\x00b"
        )
    with pytest.raises(DocumentTooLargeError, match="size limit"):
        document_parser.parse(
            filename="large.md",
            content_type="text/markdown",
            data=b"a" * (64 * 1024 + 1),
        )
    with pytest.raises(DocumentTooLargeError, match="text exceeds"):
        parser(extracted_max_chars=8).parse(
            filename="long.txt", content_type="text/plain", data=b"nine chars"
        )


def test_pdf_signature_encryption_page_and_text_limits_are_enforced() -> None:
    with pytest.raises(DocumentParseError, match="signature"):
        parser().parse(
            filename="fake.pdf", content_type="application/pdf", data=b"not a PDF"
        )
    with pytest.raises(DocumentParseError, match="Encrypted"):
        parser().parse(
            filename="private.pdf",
            content_type="application/pdf",
            data=make_pdf("secret", encrypted=True),
        )
    with pytest.raises(DocumentTooLargeError, match="page limit"):
        parser(pdf_max_pages=1).parse(
            filename="two-pages.pdf",
            content_type="application/pdf",
            data=make_pdf("page one", "page two"),
        )
    with pytest.raises(DocumentTooLargeError, match="text exceeds"):
        parser(extracted_max_chars=4).parse(
            filename="verbose.pdf",
            content_type="application/pdf",
            data=make_pdf("more than four characters"),
        )


def test_docx_signature_archive_expansion_body_and_xml_boundaries_are_enforced() -> None:
    with pytest.raises(DocumentParseError, match="signature"):
        parser().parse(
            filename="fake.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"not a zip",
        )

    missing_body = io.BytesIO()
    with zipfile.ZipFile(missing_body, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types />")
    with pytest.raises(DocumentParseError, match="body is missing"):
        parser().parse(
            filename="missing.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=missing_body.getvalue(),
        )

    expanded = make_docx("safe", extra_files={"word/media/padding.bin": b"x" * 2_048})
    with pytest.raises(DocumentTooLargeError, match="expanded content"):
        parser(docx_max_uncompressed_bytes=1_024).parse(
            filename="expanded.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=expanded,
        )

    entity = make_docx("placeholder")
    with zipfile.ZipFile(io.BytesIO(entity)) as source:
        xml = source.read("word/document.xml").replace(
            b"<w:document",
            b"<!DOCTYPE w:document [<!ENTITY unsafe 'value'>]><w:document",
            1,
        )
    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    with pytest.raises(DocumentParseError, match="declarations"):
        parser().parse(
            filename="entity.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=malicious.getvalue(),
        )
