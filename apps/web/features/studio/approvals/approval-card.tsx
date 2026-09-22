"use client";

import { Check, ChevronDown, LoaderCircle, ShieldCheck, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { argumentFields, type ApprovalDecision, type ApprovalRecord } from "./approval-model";
import { useI18n } from "@/shared/lib/i18n";

const stateLabels = {
  pending: "Your confirmation is needed",
  approved: "Allowed · waiting for the result",
  denied: "You declined this action",
  succeeded: "Action completed",
  failed: "Action failed",
  unavailable: "This confirmation is closed",
} as const;

export function ApprovalCard({ record, busy, onDecision }: {
  record: ApprovalRecord;
  busy: boolean;
  onDecision: (decision: ApprovalDecision, note?: string) => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const id = useId();
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState<ApprovalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = record.state === "pending";
  const headingRef = useRef<HTMLElement | null>(null);
  const wasPending = useRef(pending);
  const [detailsOpen, setDetailsOpen] = useState(pending);
  useEffect(() => {
    // Keep keyboard users at the receipt when the clicked button disappears.
    // Loading an older receipt must not steal focus from the conversation.
    if (wasPending.current && !pending) headingRef.current?.focus({ preventScroll: true });
    wasPending.current = pending;
  }, [pending]);
  const fields = argumentFields(record.arguments);
  const description = record.description && ![
    `${record.tool} can change an external system.`,
    `The declarative UI requested ${record.tool}.`,
  ].includes(record.description) ? record.description : null;
  const submit = async (decision: ApprovalDecision) => {
    if (busy || submitting) return;
    setSubmitting(decision);
    setError(null);
    try {
      await onDecision(decision, note.trim() || undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Unable to record your decision. Try again."));
    } finally {
      setSubmitting(null);
    }
  };
  const date = record.decidedAt ? new Date(record.decidedAt) : null;
  const dateLabel = date && Number.isFinite(date.getTime()) ? date.toLocaleString(locale === "zh" ? "zh-CN" : "en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <section className="action-confirmation" data-state={record.state} aria-labelledby={`${id}-title`} aria-busy={Boolean(submitting)}>
      <div className="action-confirmation-heading">
        <ShieldCheck size={18} aria-hidden />
        <div>
          <strong id={`${id}-title`} ref={headingRef} tabIndex={-1} role="status">{t(stateLabels[record.state])}</strong>
          <p>{pending ? t("This action can change an external system. Review it before allowing it.") : record.state === "unavailable" ? t("No confirmed result is available. Check the target before retrying.") : record.decision === "approved" ? t("You allowed this action once.") : null}</p>
        </div>
        {dateLabel && <time dateTime={record.decidedAt}>{dateLabel}</time>}
      </div>
      <button type="button" className="action-details-toggle" aria-expanded={detailsOpen} aria-controls={`${id}-details`} onClick={() => setDetailsOpen(!detailsOpen)}>
        <span>{record.tool || t("Action details")}</span><ChevronDown size={15} aria-hidden />
      </button>
      <div id={`${id}-details`} className="action-details-motion" data-open={detailsOpen} inert={!detailsOpen}>
        <div className="action-details-inner">
          {record.title && record.title !== "Approve tool execution" && <p>{record.title}</p>}
          {description && <p>{description}</p>}
          <dl className="action-fields">
            {fields.map((field, index) => <div key={index}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
          </dl>
        </div>
      </div>
      {record.note && <blockquote className="action-decision-note"><span>{t("Your reason")}</span><p>{record.note}</p></blockquote>}
      {pending && <div className="action-confirmation-form">
        <label htmlFor={`${id}-note`}>{t("Reason (optional)")}</label>
        <textarea id={`${id}-note`} value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} rows={2} disabled={busy || Boolean(submitting)} aria-describedby={`${id}-hint`} />
        <small id={`${id}-hint`}>{t("Saved with your decision; does not change this action. Do not enter passwords.")}</small>
        {error && <p className="action-confirmation-error" role="alert">{error}</p>}
        <div className="action-confirmation-actions">
          <button type="button" className="button secondary" disabled={busy || Boolean(submitting)} onClick={() => void submit("denied")}>
            {submitting === "denied" ? <LoaderCircle size={14} className="action-saving" aria-hidden /> : <X size={14} aria-hidden />}{t("Do not allow")}
          </button>
          <button type="button" className="button dark" disabled={busy || Boolean(submitting)} onClick={() => void submit("approved")}>
            {submitting === "approved" ? <LoaderCircle size={14} className="action-saving" aria-hidden /> : <Check size={14} aria-hidden />}{t("Allow once")}
          </button>
        </div>
      </div>}
    </section>
  );
}
