import assert from "node:assert/strict";
import test from "node:test";

import type { AttachmentResource } from "@alcuin/contracts";

import {
  attachmentUploadReducer,
  classifyAttachment,
  validateAttachmentFiles,
} from "./attachment-upload-model.ts";
import type { ComposerAttachment } from "./types.ts";

function draft(file: File, kind: "image" | "document"): ComposerAttachment {
  return {
    localId: "local_1", uploadId: "upload_1", file, kind, name: file.name,
    mediaType: file.type, sizeBytes: file.size, status: "queued", objectUrl: null,
    resource: null, error: null,
  };
}

test("validates the four supported image/document families and limits", () => {
  const image = new File(["image"], "map.png", { type: "image/png" });
  const pdf = new File(["pdf"], "brief.pdf", { type: "application/pdf" });
  const svg = new File(["svg"], "unsafe.svg", { type: "image/svg+xml" });
  const disguisedSvg = new File(["svg"], "unsafe.png", { type: "image/svg+xml" });
  const mismatchedImage = new File(["jpeg"], "mismatch.png", { type: "image/jpeg" });
  const mismatchedDocument = new File(["pdf"], "mismatch.docx", { type: "application/pdf" });
  const extensionOnly = new File(["markdown"], "notes.md");
  assert.equal(classifyAttachment(image), "image");
  assert.equal(classifyAttachment(pdf), "document");
  assert.equal(classifyAttachment(svg), null);
  assert.equal(classifyAttachment(disguisedSvg), null, "an explicit unsupported MIME must not be overridden by the extension");
  assert.equal(classifyAttachment(mismatchedImage), null, "an explicit MIME must match the filename extension");
  assert.equal(classifyAttachment(mismatchedDocument), null, "document MIME and filename extension must agree");
  assert.equal(classifyAttachment(extensionOnly), "document", "extension fallback is allowed only when MIME is absent");
  const result = validateAttachmentFiles([image, pdf, svg, disguisedSvg], []);
  assert.deepEqual(result.accepted.map((item) => item.kind), ["image", "document"]);
  assert.deepEqual(result.issues, ["unsupported_type"]);
});

test("enforces count, per-file, and total byte boundaries", () => {
  const sized = (name: string, type: string, size: number) => {
    const file = new File(["x"], name, { type });
    Object.defineProperty(file, "size", { value: size });
    return file;
  };
  const oversizedImage = sized("large.png", "image/png", 5 * 1024 * 1024 + 1);
  assert.deepEqual(validateAttachmentFiles([oversizedImage], []).issues, ["too_large"]);
  const documents = [1, 2, 3].map((index) => sized(`doc-${index}.pdf`, "application/pdf", 7 * 1024 * 1024));
  const total = validateAttachmentFiles(documents, []);
  assert.equal(total.accepted.length, 2);
  assert.deepEqual(total.issues, ["total_too_large"]);
  const existing = [1, 2, 3, 4].map((index) => draft(sized(`image-${index}.png`, "image/png", 10), "image"));
  assert.deepEqual(validateAttachmentFiles([sized("fifth.png", "image/png", 10)], existing).issues, ["too_many"]);
});

test("tracks real queued, processing, ready, retry, and removal states", () => {
  const file = new File(["report"], "brief.md", { type: "text/markdown" });
  const item = draft(file, "document");
  let state = attachmentUploadReducer([], { type: "queued", attachments: [item] });
  state = attachmentUploadReducer(state, { type: "started", localId: item.localId, phase: "processing" });
  assert.equal(state[0]?.status, "processing");
  state = attachmentUploadReducer(state, { type: "failed", localId: item.localId, message: "parse failed" });
  assert.equal(state[0]?.status, "error");
  state = attachmentUploadReducer(state, { type: "retry", localId: item.localId });
  assert.equal(state[0]?.status, "queued");
  const resource = {
    id: "att_1", workspace_id: "ws_1", kind: "document", name: "brief.md",
    media_type: "text/markdown", size_bytes: file.size, status: "ready",
    document: { format: "markdown", extracted_chars: 6 },
    created_at: "2026-08-29T00:00:00Z", expires_at: "2026-08-29T01:00:00Z",
  } satisfies AttachmentResource;
  state = attachmentUploadReducer(state, { type: "ready", localId: item.localId, resource });
  assert.equal(state[0]?.resource?.id, "att_1");
  state = attachmentUploadReducer(state, { type: "removed", localId: item.localId });
  assert.deepEqual(state, []);
});
