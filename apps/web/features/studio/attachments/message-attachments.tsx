"use client";

import type { AttachmentResource } from "@alcuin/contracts";
import NextImage from "next/image";
import { Download, FileText, Image as ImageIcon, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import type { ConversationAttachment } from "@/features/studio/thread-session-reducer";
import { alcuinApi } from "@/shared/lib/api";
import { useI18n } from "@/shared/lib/i18n";
import { AttachmentPreviewDialog } from "./attachment-preview-dialog";
import { formatAttachmentSize } from "./attachment-upload-model";

type Preview = { attachment: ConversationAttachment; url: string };

export function MessageAttachments({ attachments }: { attachments: ConversationAttachment[] }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  if (attachments.length === 0) return null;
  return <>
    <div className="message-attachments" data-count={Math.min(attachments.length, 4)}>
      {attachments.map((attachment) => attachment.kind === "image" ? (
        <MessageImage key={attachment.id} attachment={attachment} onPreview={(url) => setPreview({ attachment, url })} />
      ) : <MessageDocument key={attachment.id} attachment={attachment} />)}
    </div>
    {preview && <AttachmentPreviewDialog name={preview.attachment.name} url={preview.url} onClose={() => setPreview(null)} />}
  </>;
}

function MessageImage({ attachment, onPreview }: { attachment: ConversationAttachment; onPreview: (url: string) => void }) {
  const { t } = useI18n();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    let nextUrl: string | null = null;
    void alcuinApi.getAttachmentContent(attachment.id).then((blob) => {
      if (!active) return;
      nextUrl = URL.createObjectURL(blob);
      setUrl(nextUrl);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; if (nextUrl) URL.revokeObjectURL(nextUrl); };
  }, [attachment.id]);
  return (
    <button type="button" className="message-image" data-loading={!url && !failed || undefined} onClick={() => { if (url) onPreview(url); }} aria-label={t("Preview {name}", { name: attachment.name })} disabled={!url}>
      {url ? <NextImage src={url} alt={attachment.name} width={220} height={150} unoptimized />
        : failed ? <span className="message-attachment-fallback"><ImageIcon size={17} />{t("Preview unavailable")}</span>
          : <LoaderCircle className="attachment-loader" size={16} />}
    </button>
  );
}

function MessageDocument({ attachment }: { attachment: ConversationAttachment }) {
  const { t } = useI18n();
  const [metadata, setMetadata] = useState<AttachmentResource | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    void alcuinApi.getAttachment(attachment.id).then((value) => { if (active) setMetadata(value); }).catch(() => undefined);
    return () => { active = false; };
  }, [attachment.id]);
  const download = async () => {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const blob = await alcuinApi.getAttachmentContent(attachment.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = attachment.name;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_500);
    } catch {
      setFailed(true);
    } finally { setBusy(false); }
  };
  const pages = metadata?.document?.page_count;
  return (
    <button type="button" className="message-attachment-card" data-error={failed || undefined} title={attachment.name} onClick={() => void download()} disabled={busy}>
      <span>{busy ? <LoaderCircle className="attachment-loader" size={15} /> : <FileText size={15} />}</span>
      <div><strong>{attachment.name}</strong><small aria-live="polite">{failed ? t("Download failed · retry") : pages ? t("{size} · {pages} pages", { size: formatAttachmentSize(attachment.sizeBytes), pages }) : formatAttachmentSize(attachment.sizeBytes)}</small></div>
      <Download size={13} />
    </button>
  );
}
