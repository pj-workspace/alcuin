"use client";

import { CircleStop, Pause, Play, RotateCcw } from "lucide-react";
import type { TaskCommandName, TaskStatus } from "@alcuin/contracts";

import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";
import type { PendingTaskCommand } from "./task-session-reducer";

export type TaskControlHandlers = {
  onCommand: (
    command: Extract<TaskCommandName, "start" | "pause" | "resume" | "cancel" | "retry">,
    stepId?: string,
  ) => void;
};

export function TaskControls({
  status,
  pendingCommands = [],
  disabled = false,
  onCommand,
  compact = false,
  retryStepId,
}: {
  status: TaskStatus;
  pendingCommands?: PendingTaskCommand[];
  disabled?: boolean;
  onCommand: TaskControlHandlers["onCommand"];
  compact?: boolean;
  retryStepId?: string | null;
}) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  const pending = new Set(pendingCommands.map((item) => item.command));
  const busy = disabled || pending.size > 0;
  const controls = controlsForStatus(status, retryStepId);

  if (controls.length === 0 && pendingCommands.length === 0) return null;

  return (
    <div className="task-controls" data-compact={compact || undefined} aria-label={copy.taskActivity}>
      {pendingCommands.map((item) => (
        <span className="task-command-pending" role="status" key={item.id}>
          <span className="task-command-pending-dot" />
          {item.label ?? commandLabel(item.command, copy)} · {copy.queued}
        </span>
      ))}
      <div className="task-control-buttons">
        {controls.map(({ command, stepId }) => (
          <button
            type="button"
            className={command === "cancel" ? "task-control danger" : "task-control"}
            disabled={busy || pending.has(command)}
            onClick={() => onCommand(command, stepId)}
            key={`${command}:${stepId ?? "task"}`}
          >
            <ControlIcon command={command} />
            <span>{commandLabel(command, copy)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function controlsForStatus(
  status: TaskStatus,
  retryStepId?: string | null,
): Array<{ command: "start" | "pause" | "resume" | "cancel" | "retry"; stepId?: string }> {
  switch (status) {
    case "running":
      return [{ command: "pause" }, { command: "cancel" }];
    case "planning":
      return [{ command: "cancel" }];
    case "ready":
      return [{ command: "start" }, { command: "cancel" }];
    case "paused":
      return [{ command: "resume" }, { command: "cancel" }];
    case "waiting_for_approval":
      return [{ command: "cancel" }];
    case "waiting_for_user":
      return retryStepId
        ? [{ command: "retry", stepId: retryStepId }, { command: "cancel" }]
        : [{ command: "cancel" }];
    default:
      return [];
  }
}

function ControlIcon({ command }: { command: "start" | "pause" | "resume" | "cancel" | "retry" }) {
  if (command === "pause") return <Pause size={13} fill="currentColor" />;
  if (command === "resume" || command === "start") return <Play size={13} fill="currentColor" />;
  if (command === "retry") return <RotateCcw size={13} />;
  return <CircleStop size={13} />;
}

function commandLabel(command: TaskCommandName, copy: ReturnType<typeof taskCopy>): string {
  if (command === "pause") return copy.pause;
  if (command === "resume") return copy.resume;
  if (command === "start") return copy.start;
  if (command === "retry") return copy.retry;
  if (command === "cancel") return copy.stop;
  return command === "queue" ? copy.queued : command;
}
