# `alcuin-documents`

Provider-neutral, bounded parsing and editable document export.

The package recognizes and extracts text from UTF-8 TXT, Markdown, PDF, and DOCX files. It
does not index documents, access Workspaces, invoke models, or persist uploads. Callers inject
`DocumentLimits`, so conversation attachments and governed knowledge ingestion can enforce
different size, extraction, page, and DOCX expansion budgets without duplicating parser code.

PDFs are signature checked, encrypted PDFs are rejected, and page/text extraction is bounded.
DOCX parsing rejects invalid archives, oversized expanded content, missing document bodies, and
XML entity/DOCTYPE declarations. Embedded scripts, macros, relationships, and external resources
are never executed or resolved.

`export_artifact_docx(title=..., content=..., media_type=..., citations=...)` returns DOCX
bytes from Markdown, plain text, or valid JSON. It preserves editable headings, lists,
tables, code, and safe hyperlinks with Letter portrait layout and CJK font fallbacks.
Markdown images remain text descriptions; the exporter never fetches external resources.
HTML artifacts use a separate HTML download path and are rejected by this exporter.

Callers authorize the Artifact and provide only its own Run's citation registry. Exact
`[[cite:ID]]` markers resolve through `citation_id`, keeping registry numbering stable and
adding the used sources once. Unknown IDs stay visible as unavailable sources. Document
and chunk identifiers never imply a PDF page number. Size and table limits bound export
work; unsupported content raises `DocumentExportError`.

`export_artifact_markdown(content, citations=...)` preserves Markdown formatting and
literal code samples while replacing exact citation markers with portable links and a
source list. Knowledge locators stay readable metadata, not invented public links.
Unknown markers are explicitly unavailable. The API uses this path for Markdown
downloads; JSON exports remain literal code, not interpreted citation-bearing text.

DOCX visual verification requires a renderer with CJK fonts installed. Font declarations
in a DOCX do not embed font files. For local bundled LibreOffice QA, use a fontconfig
configuration that can see an installed CJK face (for example Heiti SC on macOS).
