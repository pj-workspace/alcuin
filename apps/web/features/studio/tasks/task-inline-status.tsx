"use client";

import { ChevronRight } from "lucide-react";
import type { OrbState } from "thinking-orbs";
import type { TaskStatus } from "@alcuin/contracts";

import { AgentPresenceOrb } from "@/features/studio/agent-presence-orb";
import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";
import { TaskControls, type TaskControlHandlers } from "./task-controls";
import type { PendingTaskCommand, TaskSessionPhase } from "./task-session-reducer";
import {
  isTaskWaiting,
  isTaskWorking,
  type TaskProjection,
} from "./task-projection";

export function TaskInlineStatus({
  projection,
  phase = "ready",
  pendingCommands = [],
  onCommand,
  onOpen,
}: {
  projection: TaskProjection;
  phase?: TaskSessionPhase;
  pendingCommands?: PendingTaskCommand[];
  onCommand?: TaskControlHandlers["onCommand"];
  onOpen?: () => void;
}) {
  const task = projection.task;
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  if (!task) return null;

  const currentStep = task.plan.steps.find((step) => step.id === task.current_step_id)
    ?? task.plan.steps.find((step) => step.status === "running" || step.status.startsWith("waiting_"));
  const waiting = isTaskWaiting(task.status);
  const working = isTaskWorking(task.status) && !waiting;
  const label = phase === "reconnecting" ? copy.reconnecting : statusLabel(task.status, copy);

  return (
    <section
      className="task-inline-status"
      data-task-status={task.status}
      data-working={working || undefined}
      data-waiting={waiting || undefined}
      aria-label={`${copy.task}: ${task.goal}`}
    >
      <button
        type="button"
        className="task-inline-summary"
        onClick={onOpen}
        disabled={!onOpen}
        aria-label={`${copy.taskActivity}: ${label}`}
      >
        <AgentPresenceOrb state={orbState(task.status)} active={working} size={22} />
        <span className="task-inline-copy">
          <span className="task-inline-kicker" aria-live="polite">
            {label}<i aria-hidden>·</i>{projection.progress.completed}/{projection.progress.total}
          </span>
          <strong>{currentStep?.title ?? (task.status === "completed" ? copy.result : copy.noCurrentStep)}</strong>
        </span>
        <span className="task-inline-progress" aria-label={`${copy.progress}: ${projection.progress.percent}%`}>
          <span style={{ "--task-progress": `${projection.progress.percent}%` } as React.CSSProperties} />
        </span>
        {onOpen && <ChevronRight className="task-inline-chevron" size={15} />}
      </button>
      {onCommand && (
        <TaskControls
          compact
          status={task.status}
          pendingCommands={pendingCommands}
          onCommand={onCommand}
          retryStepId={task.status === "waiting_for_user" && currentStep?.status === "failed" ? currentStep.id : null}
        />
      )}
    </section>
  );
}

function orbState(status: TaskStatus): OrbState {
  if (status === "running") return "solving";
  if (status === "planning") return "weaving";
  if (status === "pause_requested" || status === "cancel_requested") return "weaving";
  if (status === "ready") return "connecting";
  if (status === "completed") return "composing";
  return "breathing";
}

function statusLabel(status: TaskStatus, copy: ReturnType<typeof taskCopy>): string {
  if (status === "running") return copy.running;
  if (status === "planning") return copy.planning;
  if (status === "waiting_for_approval") return copy.waitingForApproval;
  if (status === "waiting_for_user") return copy.waitingForUser;
  if (status === "pause_requested") return copy.pauseRequested;
  if (status === "paused") return copy.paused;
  if (status === "cancel_requested") return copy.cancelRequested;
  if (status === "completed") return copy.completed;
  if (status === "failed") return copy.failed;
  if (status === "cancelled") return copy.cancelled;
  if (status === "ready") return copy.ready;
  return copy.draft;
}
