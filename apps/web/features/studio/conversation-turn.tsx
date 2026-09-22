"use client";

import { memo } from "react";

import { MessageAttachments } from "@/features/studio/attachments";
import { RunOutput } from "@/features/studio/run-output";
import type { ConversationTurn as ConversationTurnModel } from "@/features/studio/thread-session-reducer";
import { useI18n } from "@/shared/lib/i18n";
import { ApprovalCard } from "@/features/studio/approvals/approval-card";
import { approvalRecords, type ApprovalDecision } from "@/features/studio/approvals/approval-model";
import { QuestionCard } from "@/features/studio/questions/question-card";
import { questionRecords } from "@/features/studio/questions/question-model";

export const ConversationTurn = memo(function ConversationTurn({
  turn,
  active,
  running,
  busy,
  userRef,
  agentRef,
  onCopy,
  onDecision,
  onAnswer,
}: {
  turn: ConversationTurnModel;
  active: boolean;
  running: boolean;
  busy: boolean;
  userRef?: React.Ref<HTMLElement>;
  agentRef?: React.Ref<HTMLElement>;
  onCopy: (text: string) => void | Promise<void>;
  onDecision: (runId: string, approvalId: string, decision: ApprovalDecision, note?: string) => Promise<void>;
  onAnswer: (runId: string, inputId: string, answer: string, skip: boolean) => Promise<void>;
}) {
  const { t } = useI18n();
  const approvals = approvalRecords(turn.events, turn.status);
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
            interaction={turn.runId && questionRecords(turn.events, turn.status).map((record) => <QuestionCard key={record.id} record={record} onAnswer={(answer, skip) => onAnswer(turn.runId!, record.id, answer, skip)} />)}
          />
          {turn.runId && approvals.map((record) => <ApprovalCard key={record.id} record={record} busy={busy} onDecision={(decision, note) => onDecision(turn.runId!, record.id, decision, note)} />)}
        </div>
      </article>
    </section>
  );
});
