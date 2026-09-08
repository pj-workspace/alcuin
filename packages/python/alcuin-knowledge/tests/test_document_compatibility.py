from __future__ import annotations

import alcuin_documents
import alcuin_knowledge
from alcuin_knowledge.documents import FileDocumentParser as LegacyModuleParser


def test_knowledge_reexports_the_standalone_document_contracts() -> None:
    assert alcuin_knowledge.DocumentLimits is alcuin_documents.DocumentLimits
    assert alcuin_knowledge.DocumentParser is alcuin_documents.DocumentParser
    assert alcuin_knowledge.FileDocumentParser is alcuin_documents.FileDocumentParser
    assert alcuin_knowledge.ParsedDocument is alcuin_documents.ParsedDocument
    assert alcuin_knowledge.DocumentParseError is alcuin_documents.DocumentParseError
    assert LegacyModuleParser is alcuin_documents.FileDocumentParser


def test_legacy_knowledge_parser_import_preserves_document_behavior() -> None:
    parsed = LegacyModuleParser(
        alcuin_knowledge.DocumentLimits(
            upload_max_bytes=1_024,
            extracted_max_chars=1_024,
            pdf_max_pages=2,
        )
    ).parse(
        filename="guide.md",
        content_type="text/markdown",
        data=b"# Guide\n\nExisting knowledge imports remain compatible.",
    )

    assert parsed.title == "guide"
    assert parsed.content.endswith("Existing knowledge imports remain compatible.")
    assert parsed.metadata["document_kind"] == "markdown"
