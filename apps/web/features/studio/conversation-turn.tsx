"use client";

import { Check, FileText, ShieldCheck, X } from "lucide-react";

import { MessageAttachments } from "@/features/studio/message-attachments";
import { RunOutput } from "@/features/studio/run-output";
import type { ConversationTurn as ConversationTurnModel } from "@/features/studio/thread-session-reducer";
import { useI18n } from "@/shared/lib/i18n";

export function ConversationTurn({
  turn,
  active,
  running,
  busy,
  userRef,
  agentRef,
  onCopy,
  onDecision,
}: {
  turn: ConversationTurnModel;
  active: boolean;
  running: boolean;
  busy: boolean;
  userRef?: React.Ref<HTMLElement>;
  agentRef?: React.Ref<HTMLElement>;
  onCopy: (text: string) => void;
  onDecision: (runId: string, approvalId: string, decision: "approved" | "denied") => void;
}) {
  const { t } = useI18n();
  const approval = [...turn.events].reverse().find((event) => event.type === "approval.required");
  const completed = turn.events.some((event) => event.type === "run.completed");
  const citations = uniqueCitations(turn.events);

  return (
    <section className="conversation-turn" data-entering={turn.optimistic || undefined} data-active={active || undefined}>
      <article className="user-turn" ref={userRef} aria-label={t("You said")}>
        <div className="user-message">
          <MessageAttachments attachments={turn.attachments} />
          {turn.input && <p>{turn.input}</p>}
        </div>
      </article>
      <article className="agent-turn" ref={agentRef} aria-label={t("Agent response")}>
        <div className="agent-turn-content">
          <RunOutput
            events={turn.events}
            running={running}
            assistantText={turn.assistantText}
            onCopy={() => onCopy(turn.assistantText)}
          />
          {approval && !completed && turn.runId && (
            <div className="approval-card">
              <div className="approval-top">
                <span className="approval-icon"><ShieldCheck size={16} /></span>
                <div><strong>{approval.payload.title}</strong><p>{approval.payload.description}</p></div>
                <span className="risk-label">{t("High impact")}</span>
              </div>
              <div className="approval-command"><code>{approval.payload.tool}</code><span>{JSON.stringify(approval.payload.arguments)}</span></div>
              <div className="approval-actions">
                <button className="button secondary" disabled={busy} onClick={() => onDecision(turn.runId!, String(approval.payload.approval_id), "denied")}><X size={14} />{t("Deny")}</button>
                <button className="button dark" disabled={busy} onClick={() => onDecision(turn.runId!, String(approval.payload.approval_id), "approved")}><Check size={14} />{t("Approve once")}</button>
              </div>
            </div>
          )}
          {citations.length > 0 && (
            <button className="citation-chip" title={citations.map((event) => String(event.payload.label ?? event.payload.source ?? t("Source"))).join(" · ")}>
              <FileText size={12} />{t("Sources")} <span>{citations.length}</span>
            </button>
          )}
        </div>
      </article>
    </section>
  );
}

function uniqueCitations(events: ConversationTurnModel["events"]) {
  const seen = new Set<string>();
  return events.filter((event) => event.type === "citation.created").filter((event) => {
    const key = String(event.payload.locator ?? event.payload.label ?? event.id);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
