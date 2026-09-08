"use client";

import type { TaskStep } from "@alcuin/contracts";

import { TaskStepRow } from "./task-step-row";

export function TaskStepList({
  steps,
  selectedStepId,
  currentStepId,
  onSelect,
}: {
  steps: TaskStep[];
  selectedStepId?: string | null;
  currentStepId?: string | null;
  onSelect?: (stepId: string) => void;
}) {
  return (
    <ol className="task-step-list">
      {[...steps].sort((left, right) => left.ordinal - right.ordinal).map((step) => (
        <TaskStepRow
          key={step.id}
          step={step}
          selected={step.id === selectedStepId}
          current={step.id === currentStepId}
          onSelect={onSelect}
        />
      ))}
    </ol>
  );
}
