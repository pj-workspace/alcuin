"use client";

import { ChevronRight, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useI18n } from "@/shared/lib/i18n";

type Option = { value: string; label: string; disabled?: boolean };

export function MobileComposerSettings({ model, effort, models, efforts, disabled, contextLabel, onModel, onEffort, onContext }: {
  model: string;
  effort: string;
  models: Option[];
  efforts: Option[];
  disabled: boolean;
  contextLabel: string;
  onModel: (value: string) => void;
  onEffort: (value: string) => void;
  onContext: () => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const firstControl = useRef<HTMLSelectElement>(null);
  const id = useId();
  const close = () => { setOpen(false); trigger.current?.focus({ preventScroll: true }); };
  useEffect(() => {
    if (!open) return;
    firstControl.current?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const desktop = window.matchMedia("(min-width: 701px)");
    const resize = () => { if (desktop.matches) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    desktop.addEventListener("change", resize);
    return () => { document.removeEventListener("pointerdown", outside); desktop.removeEventListener("change", resize); };
  }, [open]);

  return <div className="mobile-composer-settings" ref={root} onKeyDown={(event) => { if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <button type="button" className="mobile-settings-trigger" ref={trigger} aria-label={t("Conversation settings")} aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <SlidersHorizontal size={17} aria-hidden /><span>{t("Settings")}</span>{(model || effort) && <i aria-hidden />}
    </button>
    <div id={id} className="mobile-composer-panel" data-open={open} role="dialog" aria-label={t("Conversation settings")} aria-hidden={!open} inert={!open}>
      <header><strong>{t("Conversation settings")}</strong><button type="button" aria-label={t("Close conversation settings")} onClick={close}><X size={18} aria-hidden /></button></header>
      <label htmlFor={`${id}-model`}>{t("Model profile")}</label>
      <select id={`${id}-model`} ref={firstControl} value={model} disabled={disabled} onChange={(event) => onModel(event.target.value)}>
        {models.map((option) => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}
      </select>
      <label htmlFor={`${id}-effort`}>{t("Thinking effort")}</label>
      <select id={`${id}-effort`} value={effort} disabled={disabled} onChange={(event) => onEffort(event.target.value)}>
        {efforts.map((option) => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}
      </select>
      <button type="button" className="mobile-context-action" onClick={() => { setOpen(false); onContext(); }}><span>{contextLabel}</span><ChevronRight size={16} aria-hidden /></button>
    </div>
  </div>;
}
