"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import { alcuinApi } from "@/shared/lib/api";
import {
  attachmentUploadReducer,
  normalizedMediaType,
  validateAttachmentFiles,
} from "./attachment-upload-model";
import type { AttachmentValidationIssue, ComposerAttachment } from "./types";

export function useComposerAttachments() {
  const [items, dispatch] = useReducer(attachmentUploadReducer, []);
  const itemsRef = useRef(items);
  const inFlightRef = useRef(new Set<string>());
  const removedUploadsRef = useRef(new Set<string>());

  useEffect(() => { itemsRef.current = items; }, [items]);

  useEffect(() => {
    for (const item of items) {
      if (item.status !== "queued" || inFlightRef.current.has(item.uploadId)) continue;
      inFlightRef.current.add(item.uploadId);
      dispatch({ type: "started", localId: item.localId, phase: item.kind === "document" ? "processing" : "uploading" });
      void alcuinApi.uploadAttachment(item.file, item.uploadId)
        .then(async (resource) => {
          if (removedUploadsRef.current.has(item.uploadId)) {
            await alcuinApi.deleteAttachment(resource.id).catch(() => undefined);
            return;
          }
          dispatch({ type: "ready", localId: item.localId, resource });
        })
        .catch((reason) => {
          if (removedUploadsRef.current.has(item.uploadId)) return;
          dispatch({
            type: "failed",
            localId: item.localId,
            message: reason instanceof Error ? reason.message : "Attachment upload failed",
          });
        })
        .finally(() => { inFlightRef.current.delete(item.uploadId); });
    }
  }, [items]);

  useEffect(() => () => {
    for (const item of itemsRef.current) revokeObjectUrl(item.objectUrl);
  }, []);

  const addFiles = useCallback((fileList: File[] | FileList): AttachmentValidationIssue[] => {
    const { accepted, issues } = validateAttachmentFiles(Array.from(fileList), itemsRef.current);
    const next = accepted.map(({ file, kind }): ComposerAttachment => ({
      localId: `local-${crypto.randomUUID()}`,
      uploadId: crypto.randomUUID(),
      file,
      kind,
      name: file.name,
      mediaType: normalizedMediaType(file, kind),
      sizeBytes: file.size,
      status: "queued",
      objectUrl: kind === "image" ? URL.createObjectURL(file) : null,
      resource: null,
      error: null,
    }));
    if (next.length > 0) {
      itemsRef.current = [...itemsRef.current, ...next];
      dispatch({ type: "queued", attachments: next });
    }
    return issues;
  }, []);

  const retry = useCallback((localId: string) => {
    const item = itemsRef.current.find((candidate) => candidate.localId === localId);
    if (!item || item.status !== "error") return;
    removedUploadsRef.current.delete(item.uploadId);
    dispatch({ type: "retry", localId });
  }, []);

  const remove = useCallback((localId: string) => {
    const item = itemsRef.current.find((candidate) => candidate.localId === localId);
    if (!item) return;
    removedUploadsRef.current.add(item.uploadId);
    revokeObjectUrl(item.objectUrl);
    itemsRef.current = itemsRef.current.filter((candidate) => candidate.localId !== localId);
    dispatch({ type: "removed", localId });
    if (item.resource) void alcuinApi.deleteAttachment(item.resource.id).catch(() => undefined);
  }, []);

  const clearAccepted = useCallback(() => {
    for (const item of itemsRef.current) revokeObjectUrl(item.objectUrl);
    itemsRef.current = [];
    dispatch({ type: "cleared" });
  }, []);

  const readyResources = useMemo(
    () => items.flatMap((item) => item.status === "ready" && item.resource ? [item.resource] : []),
    [items],
  );

  return {
    items,
    addFiles,
    retry,
    remove,
    clearAccepted,
    readyResources,
    busy: items.some((item) => item.status === "queued" || item.status === "uploading" || item.status === "processing"),
    hasErrors: items.some((item) => item.status === "error"),
  };
}

function revokeObjectUrl(url: string | null) {
  if (url) URL.revokeObjectURL(url);
}

export type ComposerAttachmentController = ReturnType<typeof useComposerAttachments>;
