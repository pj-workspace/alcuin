"use client";

import NextImage from "next/image";
import { Check, Clock3, FileText, LoaderCircle, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import { formatAttachmentSize } from "./attachment-upload-model";
import type { ComposerAttachment } from "./types";
import { useI18n } from "@/shared/lib/i18n";

export function ComposerAttachmentTray({
  items,
  onRetry,
  onRemove,
}: {
  items: ComposerAttachment[];
  onRetry: (localId: string) => void;
  onRemove: (localId: string) => void;
}) {
  const { t } = useI18n();
  const [removing, setRemoving] = useState<Set<string>>(() => new Set());
  if (items.length === 0) return null;
  const remove = (item: ComposerAttachment) => {
    if (removing.has(item.localId)) return;
    setRemoving((current) => new Set(current).add(item.localId));
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.setTimeout(() => onRemove(item.localId), reduced ? 0 : 160);
  };
  return (
    <div className="composer-attachments" role="list" aria-label={t("Attached files")}>
      {items.map((item) => (
        <article className="composer-attachment" data-kind={item.kind} data-status={item.status} data-removing={removing.has(item.localId) || undefined} role="listitem" key={item.localId}>
          <div className="composer-attachment-visual">
            {item.kind === "image" && item.objectUrl ? (
              <NextImage src={item.objectUrl} alt="" width={54} height={46} unoptimized />
            ) : <FileText size={18} />}
            <span className="composer-attachment-state" aria-hidden="true">
              <span key={item.status}>
                {item.status === "ready" ? <Check size={10} />
                  : item.status === "queued" ? <Clock3 size={10} />
                    : item.status === "error" ? <X size={10} />
                      : <LoaderCircle size={11} />}
              </span>
            </span>
          </div>
          <div className="composer-attachment-copy">
            <strong title={item.name}>{item.name}</strong>
            <small aria-live="polite">{attachmentStatusLabel(item, t)}</small>
          </div>
          <div className="composer-attachment-actions">
            {item.status === "error" && <button type="button" onClick={() => onRetry(item.localId)} aria-label={t("Retry {name}", { name: item.name })}><RotateCcw size={11} /></button>}
            <button type="button" onClick={() => remove(item)} aria-label={t("Remove {name}", { name: item.name })}><X size={11} /></button>
          </div>
        </article>
      ))}
    </div>
  );
}

function attachmentStatusLabel(item: ComposerAttachment, t: ReturnType<typeof useI18n>["t"]): string {
  if (item.status === "queued") return t("Queued");
  if (item.status === "uploading") return t("Uploading…");
  if (item.status === "processing") return t("Processing…");
  if (item.status === "error") return t("Upload failed");
  const pages = item.resource?.document?.page_count;
  const details = pages ? t("{size} · {pages} pages", { size: formatAttachmentSize(item.sizeBytes), pages }) : formatAttachmentSize(item.sizeBytes);
  return t("Ready · {details}", { details });
}
