# ADR-0010: Independent Artifact output and Run-local evidence

- Status: Accepted; implementation requires ordinary integration and UI verification
- Date: 2026-09-08

## Context

Mirroring every assistant answer into the Canvas duplicated conversation text and made a
generated deliverable indistinguishable from commentary. Source badges also lacked stable
identity shared by model output, source inspection, and exported documents. Tasks discarded
the temporary model controls selected in the chat composer.

## Decisions

### Separate conversational output from deliverables

The provider-neutral output protocol uses `<alcuin-answer>…</alcuin-answer>` for final
output and nested `<alcuin-artifact title="…" content-type="…">…</alcuin-artifact>` for
requested files. A committed answer streams without a synthetic `done` tool. Tool-call
preambles remain execution progress; providers must not call tools after committing final
output. The incremental parser recognizes delimiters across arbitrary chunk boundaries and
preserves literal examples inside Markdown code. It rejects malformed or unfinished output.

Ordinary and legacy unmarked answers remain conversation content. They are not automatically
promoted into Artifacts. A Run may create at most eight separately identified Artifacts,
each limited to 500,000 characters. Markdown, plain text, HTML, and valid JSON are supported.
JSON waits for its complete block before persistence. Runtime events carry bounded full
snapshots; canonical content, internal revisions, and events remain atomic. Internal revision
numbers support replay and concurrent edits, not a version-management product workflow.

### Reserve one consistent output budget and honor provider terminal state

Context assembly and provider generation share `context_reserved_output_tokens`, normally
16,384 tokens. Chat Completions sends it as `max_tokens`; Responses sends it as
`max_output_tokens`. There is no model-specific 4,096-token cap. When a small context window
cannot accommodate normal defaults, only unspecified reserves are scaled; explicit settings
remain authoritative and incompatible totals are rejected.

Provider output truncation, content filtering, failed/incomplete Responses, and missing
terminal signals emit structured `run.failed` events. Text and Artifact snapshots already
received remain available as partial output. A response containing only reasoning or an
empty final-answer block cannot become `run.completed`. Truncated tool calls are rejected
before dispatch. These checks preserve the selected model and thinking effort rather than
automatically changing either profile or reporting an empty response as success.

### Give retrieved evidence exact Run-local identity

The runtime assigns `citation_id` values to retrieved sources, deduplicates canonical
locators, and seeds the registry from the same Run's persisted events when resuming approval.
Tools expose these IDs with bounded locators and snippets to the model. Source content is
untrusted reference data. The model emits `[[cite:s1]]` next to supported claims; clients
resolve exact IDs only, preserving registry source order in visible numbering.

Source details include available labels, snippets, locators, and knowledge document/chunk
metadata. Unknown or ambiguous IDs must not link confidently to a source. Chunk indices are
not page numbers, and source identity does not establish automatic factual verification.
Markdown Artifacts and Word exports use only the originating Run's evidence registry.

### Render HTML locally and export editable text documents

HTML previews use an iframe with scripts allowed but no same-origin privilege. A restrictive
content policy blocks network access, remote libraries, forms, and navigation. Inline local
controls and SVG may respond to user actions inside the preview. The HTML source can be
downloaded separately; external execution of that downloaded file is outside Studio's
sandbox boundary.

`alcuin-documents.export_artifact_docx` is a bounded, provider-neutral bytes exporter. It
converts Markdown, plain text, or JSON into editable Word elements, including headings,
lists, tables, code, and safe hyperlinks. It loads no external images or resources. Word
references preserve the Run registry numbering and list the used sources; unavailable
references remain explicit. HTML-to-Word conversion, arbitrary binary formats, and complete
Markdown/office fidelity are not claimed. The download service checks Workspace ownership
and the originating Run's terminal state before exporting.

### Carry selected model controls through durable Tasks

Task creation persists `model_override` and `reasoning_effort` and validates them using the
same catalog resolver as ordinary chat. Every new Step/Attempt revalidates the saved values,
including retry and restart recovery. A removed or incompatible catalog model fails before
creating another Run. Default controls use the Thread's pinned Agent definition. Tasks
continue to execute explicit steps sequentially; automatic planning and multi-agent runtime
are separate unimplemented capabilities.

## Verification boundaries

Parser chunk-boundary, citation identity, DOCX structure, Task control persistence, and
Workspace isolation tests provide contract evidence. Rendered Word files and actual Studio
flows still require visual and integration checks; unit tests alone do not establish UI
quality, provider availability, production failover, or exactly-once external effects.

Quick embedding, Artifact history/rollback/publishing, and other version-management product
work remain frozen while Agent capability and frontend interaction are prioritized.
