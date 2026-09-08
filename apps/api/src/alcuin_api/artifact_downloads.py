"""Safe export selection; persistence and authorization stay at the API boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from alcuin_documents.export import export_artifact_docx, export_artifact_markdown


def export_artifact(
    artifact: Mapping[str, Any],
    output_format: str,
    *,
    citations: Sequence[Mapping[str, Any]] = (),
) -> tuple[bytes, str, str]:
    content_type = str(artifact["content_type"])
    content = str(artifact["content"])
    if output_format == "docx":
        if content_type == "text/html":
            raise ValueError(
                "HTML artifacts export as HTML; use a document artifact for Word"
            )
        return (
            export_artifact_docx(
                title=str(artifact["title"]),
                content=content,
                media_type=content_type,
                citations=citations,
            ),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        )
    if output_format == "html" and content_type == "text/html":
        return content.encode("utf-8"), "text/html; charset=utf-8", "html"
    if output_format == "md" and content_type in {
        "text/markdown",
        "text/plain",
        "application/json",
    }:
        if content_type == "application/json":
            content = "```json\n" + content + "\n```\n"
        else:
            content = export_artifact_markdown(content, citations=citations)
        return content.encode("utf-8"), "text/markdown; charset=utf-8", "md"
    raise ValueError("Requested export format is incompatible with this artifact")
