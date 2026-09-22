"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { Check, MessageCircleQuestion } from "lucide-react";
import { useI18n } from "@/shared/lib/i18n";
import type { QuestionRecord } from "./question-model";

gsap.registerPlugin(useGSAP);

export function QuestionCard({ record, onAnswer }: {
  record: QuestionRecord;
  onAnswer: (answer: string, skip: boolean) => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const id = useId();
  const root = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const lock = useRef(false);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = record.state === "pending";
  const wasPending = useRef(pending);
  useEffect(() => {
    if (wasPending.current && !pending) heading.current?.focus({ preventScroll: true });
    wasPending.current = pending;
  }, [pending]);
  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo(root.current, { opacity: 0.5, y: 5 }, { opacity: 1, y: 0, duration: 0.18, clearProps: "opacity,transform" });
    });
    return () => media.revert();
  }, { scope: root, dependencies: [record.state], revertOnUpdate: true });
  const submit = async (skip: boolean) => {
    if (!pending || lock.current || (!skip && !answer.trim())) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try { await onAnswer(skip ? "" : answer.trim(), skip); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("Unable to save your answer. Try again.")); }
    finally { lock.current = false; setBusy(false); }
  };
  const date = record.answeredAt ? new Date(record.answeredAt) : null;
  return (
    <section className="agent-question" ref={root} data-state={record.state} aria-labelledby={`${id}-heading`} aria-busy={busy}>
      <div className="agent-question-heading">
        {record.state === "answered" ? <Check size={17} aria-hidden /> : <MessageCircleQuestion size={17} aria-hidden />}
        <h3 id={`${id}-heading`} ref={heading} tabIndex={-1}>{t(pending ? "A question for you" : record.state === "answered" ? "Your answer" : record.state === "skipped" ? "Continued without an answer" : "This question is closed")}</h3>
        {date && Number.isFinite(date.getTime()) && <time dateTime={record.answeredAt}>{date.toLocaleTimeString(locale === "zh" ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" })}</time>}
      </div>
      <p className="agent-question-prompt">{record.question}</p>
      {pending ? <form onSubmit={(event) => { event.preventDefault(); void submit(false); }}>
        {record.options.length > 0 && <fieldset disabled={busy} className="agent-question-options"><legend className="visually-hidden">{t("Suggested answers")}</legend>
          {record.options.map((option, index) => <label key={option} data-selected={answer === option}>
            <input type="radio" name={`${id}-option`} value={option} checked={answer === option} onChange={() => setAnswer(option)} />
            <span className="agent-question-number" aria-hidden>{index + 1}</span><span>{option}</span>
          </label>)}
        </fieldset>}
        <label className="agent-question-answer-label" htmlFor={`${id}-answer`}>{t("Your answer or additional details")}</label>
        <textarea id={`${id}-answer`} rows={2} maxLength={4000} value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={busy} aria-describedby={`${id}-hint`} />
        <small id={`${id}-hint`}>{t("Do not enter passwords or API keys.")}</small>
        {error && <p role="alert" className="action-confirmation-error">{error}</p>}
        <div className="agent-question-actions">
          <button className="button secondary" type="button" disabled={busy} onClick={() => void submit(true)}>{t("Skip and let AI continue")}</button>
          <button className="button dark" type="submit" disabled={busy || !answer.trim()}>{t(busy ? "Saving answer…" : "Answer and continue")}</button>
        </div>
      </form> : record.state === "answered" ? <blockquote className="agent-question-receipt">{record.answer}</blockquote> : null}
    </section>
  );
}
