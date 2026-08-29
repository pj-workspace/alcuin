"use client";

import NextImage from "next/image";
import { FileImage, X } from "lucide-react";
import { useState } from "react";

import type { ConversationAttachment } from "@/features/studio/thread-session-reducer";
import { useI18n } from "@/shared/lib/i18n";

export function MessageAttachments({ attachments }: { attachments: ConversationAttachment[] }) {
  const { t } = useI18n();
  const [preview, setPreview] = useState<ConversationAttachment | null>(null);
  if (attachments.length === 0) return null;

  return <>
    <div className="message-attachments" data-count={Math.min(attachments.length, 4)}>
      {attachments.map((attachment) => attachment.dataUrl ? (
        <button
          type="button"
          className="message-image"
          key={attachment.id}
          onClick={() => setPreview(attachment)}
          aria-label={t("Preview {name}", { name: attachment.name })}
        >
          <NextImage
            src={attachment.dataUrl}
            alt={attachment.name}
            width={220}
            height={150}
            unoptimized
          />
        </button>
      ) : (
        <div className="message-attachment-card" key={attachment.id} title={attachment.name}>
          <span><FileImage size={15} /></span>
          <div><strong>{attachment.name}</strong><small>{attachment.mediaType}</small></div>
        </div>
      ))}
    </div>
    {preview?.dataUrl && (
      <div className="attachment-preview-backdrop" role="dialog" aria-modal="true" aria-label={preview.name} onMouseDown={() => setPreview(null)}>
        <div className="attachment-preview" onMouseDown={(event) => event.stopPropagation()}>
          <button type="button" onClick={() => setPreview(null)} aria-label={t("Close preview")}><X size={16} /></button>
          <NextImage src={preview.dataUrl} alt={preview.name} width={1200} height={900} unoptimized />
          <span>{preview.name}</span>
        </div>
      </div>
    )}
  </>;
}
