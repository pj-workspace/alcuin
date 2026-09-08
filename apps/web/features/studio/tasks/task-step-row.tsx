"use client";

import {
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  CircleStop,
  Clock3,
  Minus,
  ShieldQuestion,
} from "lucide-react";
import { clsx } from "clsx";
import type { TaskStep, TaskStepStatus } from "@alcuin/contracts";

import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";

export function TaskStepRow({
  step,
  selected = false,
  current = false,
  onSelect,
}: {
  step: TaskStep;
  selected?: boolean;
  current?: boolean;
  onSelect?: (stepId: string) => void;
}) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  const waiting = step.status === "waiting_for_approval" || step.status === "waiting_for_user";

  return (
    <li
      className={clsx("task-step-row", selected && "selected", current && "current")}
      data-step-status={step.status}
      data-working={step.status === "running" || undefined}
      data-waiting={waiting || undefined}
    >
      <button type="button" onClick={() => onSelect?.(step.id)} disabled={!onSelect} aria-current={current ? "step" : undefined}>
        <span className="task-step-node" aria-hidden><StepIcon status={step.status} /></span>
        <span className="task-step-copy">
          <strong>{step.title}</strong>
          <span>{stepStatusLabel(step.status, copy)}{step.evidence.length > 0 && <> · {step.evidence.length} {copy.evidence.toLocaleLowerCase()}</>}</span>
        </span>
        {onSelect && <ChevronRight className="task-step-chevron" size={14} />}
      </button>
    </li>
  );
}

function StepIcon({ status }: { status: TaskStepStatus }) {
  if (status === "completed") return <Check size={12} />;
  if (status === "failed") return <CircleAlert size={12} />;
  if (status === "cancelled") return <CircleStop size={12} />;
  if (status === "skipped") return <Minus size={12} />;
  if (status === "waiting_for_approval") return <ShieldQuestion size={12} />;
  if (status === "waiting_for_user") return <Clock3 size={12} />;
  if (status === "running") return <span className="task-step-working-dot" />;
  return <Circle size={9} />;
}

function stepStatusLabel(status: TaskStepStatus, copy: ReturnType<typeof taskCopy>): string {
  if (status === "running") return copy.running;
  if (status === "waiting_for_approval") return copy.waitingForApproval;
  if (status === "waiting_for_user") return copy.waitingForUser;
  if (status === "completed") return copy.completed;
  if (status === "failed") return copy.stepFailed;
  if (status === "skipped") return copy.skipped;
  if (status === "cancelled") return copy.stepCancelled;
  return copy.pending;
}
