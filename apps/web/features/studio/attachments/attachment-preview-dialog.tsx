"use client";

import NextImage from "next/image";
import { X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { clsx } from "clsx";

import { useI18n } from "@/shared/lib/i18n";

export function AttachmentPreviewDialog({ name, url, onClose }: { name: string; url: string; onClose: () => void }) {
  const { t } = useI18n();
  const [closing, setClosing] = useState(false);
  const close = useCallback(() => {
    if (closing) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setClosing(true);
    window.setTimeout(onClose, reduced ? 0 : 180);
  }, [closing, onClose]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close]);
  return (
    <div className={clsx("attachment-preview-backdrop", closing && "closing")} role="dialog" aria-modal="true" aria-label={name} onMouseDown={close}>
      <div className="attachment-preview" onMouseDown={(event) => event.stopPropagation()}>
        <button type="button" autoFocus onClick={close} aria-label={t("Close preview")}><X size={16} /></button>
        <NextImage src={url} alt={name} width={1200} height={900} unoptimized />
        <span>{name}</span>
      </div>
    </div>
  );
}
