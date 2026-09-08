"""Compatibility exports for the standalone document parser package.

Existing callers continue importing these names from ``alcuin_knowledge.documents`` while new
conversation and ingestion services depend directly on ``alcuin_documents``.
"""

from alcuin_documents import (
    DocumentLimits,
    DocumentParseError,
    DocumentParser,
    DocumentTooLargeError,
    FileDocumentParser,
    ParsedDocument,
    UnsupportedDocumentError,
)

__all__ = [
    "DocumentLimits",
    "DocumentParseError",
    "DocumentParser",
    "DocumentTooLargeError",
    "FileDocumentParser",
    "ParsedDocument",
    "UnsupportedDocumentError",
]
