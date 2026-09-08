"use client";

import { Check, ShieldCheck, X } from "lucide-react";

import { MessageAttachments } from "@/features/studio/attachments";
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
  onCopy: (text: string) => void | Promise<void>;
  onDecision: (runId: string, approvalId: string, decision: "approved" | "denied") => void;
}) {
  const { t } = useI18n();
  const approval = [...turn.events].reverse().find((event) => event.type === "approval.required");
  const completed = turn.events.some((event) => event.type === "run.completed");
  const hasUserContent = Boolean(turn.input.trim() || turn.attachments.length > 0);

  return (
    <section className="conversation-turn" data-entering={turn.optimistic || undefined} data-active={active || undefined}>
      {hasUserContent && (
        <article className="user-turn" ref={userRef} aria-label={t("You said")}>
          <div className="user-message">
            <MessageAttachments attachments={turn.attachments} />
            {turn.input && <p>{turn.input}</p>}
          </div>
        </article>
      )}
      <article className="agent-turn" ref={agentRef} aria-label={t("Agent response")}>
        <div className="agent-turn-content">
          <RunOutput
            events={turn.events}
            runId={turn.runId}
            running={running}
            assistantText={turn.assistantText}
            onCopy={onCopy}
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
        </div>
      </article>
    </section>
  );
}
