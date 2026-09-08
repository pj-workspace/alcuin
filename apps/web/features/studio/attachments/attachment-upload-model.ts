import type { AttachmentKind, AttachmentResource } from "@alcuin/contracts";

import type { AttachmentValidationIssue, ComposerAttachment } from "./types";

export const MAX_ATTACHMENTS_PER_RUN = 4;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const MAX_DOCUMENT_BYTES = 8 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
export const ATTACHMENT_ACCEPT = ".png,.jpg,.jpeg,.webp,.txt,.md,.markdown,.pdf,.docx,image/png,image/jpeg,image/webp,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

const IMAGE_MEDIA_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const DOCUMENT_MEDIA_TYPES = new Set([
  "text/plain",
  "text/markdown",
  "text/x-markdown",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

export type AttachmentUploadAction =
  | { type: "queued"; attachments: ComposerAttachment[] }
  | { type: "started"; localId: string; phase: "uploading" | "processing" }
  | { type: "ready"; localId: string; resource: AttachmentResource }
  | { type: "failed"; localId: string; message: string }
  | { type: "retry"; localId: string }
  | { type: "removed"; localId: string }
  | { type: "cleared" };

export function attachmentUploadReducer(
  state: ComposerAttachment[],
  action: AttachmentUploadAction,
): ComposerAttachment[] {
  if (action.type === "queued") return [...state, ...action.attachments];
  if (action.type === "removed") return state.filter((item) => item.localId !== action.localId);
  if (action.type === "cleared") return [];
  return state.map((item) => {
    if (item.localId !== action.localId) return item;
    if (action.type === "started") return { ...item, status: action.phase, error: null };
    if (action.type === "ready") return {
      ...item,
      status: "ready",
      kind: action.resource.kind,
      name: action.resource.name,
      mediaType: action.resource.media_type,
      sizeBytes: action.resource.size_bytes,
      resource: action.resource,
      error: null,
    };
    if (action.type === "failed") return { ...item, status: "error", error: action.message };
    return { ...item, status: "queued", error: null };
  });
}

export function classifyAttachment(file: File): AttachmentKind | null {
  const mediaType = file.type.toLowerCase();
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  if (mediaType) {
    if (mediaType === "image/png" && extension === "png") return "image";
    if (mediaType === "image/jpeg" && ["jpg", "jpeg"].includes(extension)) return "image";
    if (mediaType === "image/webp" && extension === "webp") return "image";
    if (mediaType === "text/plain" && ["txt", "md", "markdown"].includes(extension)) return "document";
    if (["text/markdown", "text/x-markdown"].includes(mediaType) && ["md", "markdown"].includes(extension)) return "document";
    if (mediaType === "application/pdf" && extension === "pdf") return "document";
    if (mediaType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document" && extension === "docx") return "document";
    return null;
  }
  if (["png", "jpg", "jpeg", "webp"].includes(extension)) return "image";
  if (["txt", "md", "markdown", "pdf", "docx"].includes(extension)) return "document";
  return null;
}

export function normalizedMediaType(file: File, kind: AttachmentKind): string {
  const mediaType = file.type.toLowerCase();
  if (kind === "image" && IMAGE_MEDIA_TYPES.has(mediaType)) return mediaType;
  if (kind === "document" && DOCUMENT_MEDIA_TYPES.has(mediaType)) {
    return mediaType === "text/x-markdown" ? "text/markdown" : mediaType;
  }
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  if (kind === "image") return extension === "png" ? "image/png" : extension === "webp" ? "image/webp" : "image/jpeg";
  if (extension === "pdf") return "application/pdf";
  if (extension === "docx") return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  return extension === "md" || extension === "markdown" ? "text/markdown" : "text/plain";
}

export function validateAttachmentFiles(
  files: File[],
  existing: ComposerAttachment[],
): { accepted: Array<{ file: File; kind: AttachmentKind }>; issues: AttachmentValidationIssue[] } {
  const accepted: Array<{ file: File; kind: AttachmentKind }> = [];
  const issues = new Set<AttachmentValidationIssue>();
  let totalBytes = existing.reduce((sum, item) => sum + item.sizeBytes, 0);
  let slots = Math.max(0, MAX_ATTACHMENTS_PER_RUN - existing.length);

  for (const file of files) {
    if (slots <= 0) { issues.add("too_many"); continue; }
    const kind = classifyAttachment(file);
    if (!kind) { issues.add("unsupported_type"); continue; }
    const maxBytes = kind === "image" ? MAX_IMAGE_BYTES : MAX_DOCUMENT_BYTES;
    if (file.size > maxBytes) { issues.add("too_large"); continue; }
    if (totalBytes + file.size > MAX_TOTAL_BYTES) { issues.add("total_too_large"); continue; }
    accepted.push({ file, kind });
    totalBytes += file.size;
    slots -= 1;
  }
  return { accepted, issues: [...issues] };
}

export function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(sizeBytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}
