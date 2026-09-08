# ADR-0008: Workspace-scoped conversation attachments

- Status: Accepted
- Date: 2026-08-29

## Context

The first vision prototype accepted image data URLs inside `RunCreate`. That coupled the public
Run contract to one provider representation and allowed large encoded bytes to cross message,
event, context, and logging boundaries. It also provided no durable attachment identity for
document parsing, later conversation turns, Workspace authorization, retry-safe uploads, or
reloadable model input.

## Decision

### Upload resources before creating a Run

`POST /v1/attachments` creates a staged, Workspace-owned `AttachmentResource`. Clients provide a
stable `upload_id`; repeating the same upload is idempotent, while reusing that id for different
content is rejected. The public resource exposes only presentation-safe metadata:

- resource and Workspace identity;
- kind, filename, media type, byte size, readiness, and timestamps;
- for documents, format, extracted character count, and PDF page count when applicable.

Upload ids, content digests, binding state, blob storage details, bytes, and extracted text are
internal. `RunCreate` accepts at most four `attachment_ids`; it no longer accepts data URLs.

### Blob storage is behind a repository port

`attachments` owns immutable metadata and expiry, `attachment_blobs` owns bytes and extracted
document text, and `message_attachments` binds one resource to one immutable user Message. The
PostgreSQL adapter currently stores blobs as `bytea`, but runtime and application services depend
only on `AttachmentRepository`. An in-memory adapter implements the same staged-resource and
single-binding behavior for service tests.

Binding is authoritative in `message_attachments`, not duplicated in a mutable attachment status.
An attachment is staged while no binding exists. A staged attachment may be deleted; a bound
attachment may not.

### Run acceptance and attachment binding are one transaction

The repository locks the Thread and referenced attachment rows, then validates Workspace
ownership, readiness, expiry, single use, count, and total byte budget. It creates the Run, its
immutable user Message, its customization snapshot, and all attachment bindings in the same
transaction. Any validation or write failure rolls everything back, so the client can retry with
still-staged resources. API-side metadata lookup improves error messages but is not the authority.

### Provider representations are transient

Persisted message parts contain attachment references and integrity metadata only. The runtime
resolves authorized bytes immediately before provider invocation and verifies their size, media
type, and SHA-256 digest. Images are converted to a provider data URL only in memory for that
outbound request. Data URLs, raw bytes, storage keys, and provider credentials are rejected or
excluded from Messages, Events, context snapshots, and logs.

Current images require a selected model whose catalog declares image input. Historical images do
not prevent switching to a text-only model; they are simply omitted. A vision model may reload up
to four images from current and uncompacted historical Messages, with current images first.

Documents are parsed synchronously by `alcuin-documents`. Their extracted text is associated with
the owning user Message and is marked as an untrusted document boundary before entering model
context. Uncompacted later turns can therefore use the document without duplicating its bytes in
message storage.

### Formats and budgets are explicit

The implemented formats are PNG, JPEG, WebP, UTF-8 TXT, Markdown, PDF, and DOCX. SVG and GIF are
rejected. Defaults are 5 MiB per image, 8 MiB per document, 20 MiB per Run, 100 PDF pages, and
120,000 extracted characters. Content download sets `X-Content-Type-Options: nosniff`; images use
inline disposition and documents use attachment disposition.

## Consequences

- Run and message contracts remain provider-neutral and small.
- A refresh or process restart can reconstruct authorized image and document inputs by resource id.
- Workspace filters and composite foreign keys prevent cross-Workspace metadata, blob, and binding
  access.
- Upload and parsing are synchronous in this slice; asynchronous malware scanning, OCR, object
  storage adapters, retention cleanup, and document previews require later explicit contracts.
- A bound attachment belongs to one user Message. Reusing the same file requires a new staged
  resource with a new upload id.

## Verification

- Repository tests cover upload idempotency, Workspace isolation, restart reads, atomic
  failure/retry, single binding, and in-memory adapter parity.
- API tests cover the safe public contract, content authorization and headers, staged deletion,
  MIME and size rejection, total Run limits, document reuse on later turns, and text-model image
  behavior.
- Runtime tests verify digest-checked image resolution and confirm that data URLs exist only in the
  provider request, never in persisted Messages, Events, or context snapshots.
