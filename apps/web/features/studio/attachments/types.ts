import type { AttachmentKind, AttachmentResource } from "@alcuin/contracts";

export type ComposerAttachmentStatus = "queued" | "uploading" | "processing" | "ready" | "error";

export interface ComposerAttachment {
  localId: string;
  uploadId: string;
  file: File;
  kind: AttachmentKind;
  name: string;
  mediaType: string;
  sizeBytes: number;
  status: ComposerAttachmentStatus;
  objectUrl: string | null;
  resource: AttachmentResource | null;
  error: string | null;
}

export type AttachmentValidationIssue = "unsupported_type" | "too_large" | "too_many" | "total_too_large";
