"use client";

import type { RefObject } from "react";

import { AgentPresenceOrb } from "@/features/studio/agent-presence-orb";
import { ConversationTurn } from "@/features/studio/conversation-turn";
import type { ConversationTurn as ConversationTurnModel } from "@/features/studio/thread-session-reducer";
import { useI18n } from "@/shared/lib/i18n";

export function ConversationTimeline({
  turns,
  workspaceName,
  threadTitle,
  activeRunId,
  running,
  busy,
  anchorRef,
  endRef,
  spacerPx,
  onCopy,
  onDecision,
}: {
  turns: ConversationTurnModel[];
  workspaceName: string;
  threadTitle: string;
  activeRunId: string | null;
  running: boolean;
  busy: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  endRef: RefObject<HTMLElement | null>;
  spacerPx: number;
  onCopy: (text: string) => void | Promise<void>;
  onDecision: (runId: string, approvalId: string, decision: "approved" | "denied") => void;
}) {
  const { t } = useI18n();
  const latestIndex = turns.length - 1;
  return (
    <div className="conversation-inner" role="log" aria-label={t("Conversation history")} aria-busy={running}>
      <div className="thread-meta"><span>{workspaceName}</span><i />{threadTitle}<i />{t("Now")}</div>
      {turns.length === 0 ? (
        <div className="conversation-empty">
          <AgentPresenceOrb state="breathing" active size={58} className="empty-presence-orb" />
          <h2>{t("Start a conversation")}</h2>
          <p>{t("This thread will keep its context as you continue.")}</p>
        </div>
      ) : turns.map((turn, index) => (
        <ConversationTurn
          key={turn.id}
          turn={turn}
          active={index === latestIndex}
          running={running && turn.runId === activeRunId}
          busy={busy}
          userRef={index === latestIndex ? anchorRef : undefined}
          agentRef={index === latestIndex ? endRef : undefined}
          onCopy={onCopy}
          onDecision={onDecision}
        />
      ))}
      <div className="turn-stream-spacer" style={{ height: spacerPx }} aria-hidden />
      <span className="visually-hidden" role="status" aria-live="polite">
        {running ? t("Agent is responding") : ""}
      </span>
    </div>
  );
}
