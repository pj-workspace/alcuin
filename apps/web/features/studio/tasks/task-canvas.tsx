"use client";

import { CircleAlert, FileOutput, Flag, ListChecks, PencilLine, ShieldCheck } from "lucide-react";
import { clsx } from "clsx";
import { useState } from "react";

import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";
import { TaskControls, type TaskControlHandlers } from "./task-controls";
import { TaskEvidenceList } from "./task-evidence-list";
import { TaskPlanEditor } from "./task-plan-editor";
import { TaskResult } from "./task-result";
import type { TaskPlanDraft, TaskSessionState } from "./task-session-reducer";
import { TaskStepDetail } from "./task-step-detail";
import { TaskStepList } from "./task-step-list";

type TaskCanvasTab = "plan" | "evidence" | "result";

export function TaskCanvas({
  session,
  onSelectStep,
  onBeginPlanEdit,
  onPlanDraftChange,
  onSavePlan,
  onCancelPlanEdit,
  onCommand,
  onDismissError,
}: {
  session: TaskSessionState;
  onSelectStep: (stepId: string) => void;
  onBeginPlanEdit: () => void;
  onPlanDraftChange: (draft: TaskPlanDraft) => void;
  onSavePlan: () => void;
  onCancelPlanEdit: () => void;
  onCommand: TaskControlHandlers["onCommand"];
  onDismissError?: () => void;
}) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  const [tab, setTab] = useState<TaskCanvasTab>("plan");
  const task = session.projection.task;

  // A conversational turn must not gain Task UI until the server creates a Task.
  if (!task) return null;

  const selectedStep = task.plan.steps.find((step) => step.id === session.selectedStepId) ?? null;
  const progress = session.projection.progress;

  return (
    <section className="task-canvas" data-task-canvas data-task-status={task.status}>
      <header className="task-canvas-header">
        <div className="task-canvas-heading">
          <span className="task-canvas-mark"><ListChecks size={15} /></span>
          <div><small>{copy.task}</small><h2>{task.goal}</h2></div>
        </div>
        <div className="task-canvas-progress" aria-label={`${copy.progress}: ${progress.percent}%`}>
          <span><strong>{progress.completed}</strong> / {progress.total}</span>
          <i><span style={{ "--task-progress": `${progress.percent}%` } as React.CSSProperties} /></i>
        </div>
      </header>

      <nav className="task-canvas-tabs" role="tablist" aria-label={copy.taskActivity}>
        <button type="button" role="tab" aria-selected={tab === "plan"} className={clsx(tab === "plan" && "active")} onClick={() => setTab("plan")}><ListChecks size={13} />{copy.plan}<span>{task.plan.steps.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "evidence"} className={clsx(tab === "evidence" && "active")} onClick={() => setTab("evidence")}><ShieldCheck size={13} />{copy.evidence}<span>{task.evidence.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "result"} className={clsx(tab === "result" && "active")} onClick={() => setTab("result")}><FileOutput size={13} />{copy.result}</button>
      </nav>

      {session.error && (
        <div className="task-canvas-error" role="alert">
          <CircleAlert size={14} /><span>{session.error}</span>
          {onDismissError && <button type="button" onClick={onDismissError}>{copy.cancel}</button>}
        </div>
      )}

      <div className="task-canvas-body" key={tab}>
        {tab === "plan" && (
          session.planDraft ? (
            <TaskPlanEditor
              draft={session.planDraft}
              busy={session.planSaving}
              onChange={onPlanDraftChange}
              onSave={onSavePlan}
              onCancel={onCancelPlanEdit}
            />
          ) : (
            <div className="task-plan-surface">
              <div className="task-plan-toolbar">
                <div><small>{copy.currentStep}</small><strong>{selectedStep?.title ?? copy.noCurrentStep}</strong></div>
                {!session.planSaving && ["draft", "planning", "ready", "paused"].includes(task.status) && (
                  <button type="button" onClick={onBeginPlanEdit}><PencilLine size={12} />{copy.editPlan}</button>
                )}
              </div>
              <div className="task-plan-layout">
                <TaskStepList steps={task.plan.steps} selectedStepId={session.selectedStepId} currentStepId={task.current_step_id} onSelect={onSelectStep} />
                <TaskStepDetail step={selectedStep} />
              </div>
            </div>
          )
        )}
        {tab === "evidence" && (
          <div className="task-evidence-surface">
            {task.checkpoints.length > 0 && (
              <section className="task-checkpoints">
                <h3><Flag size={13} />{copy.checkpoint}</h3>
                <ol>{task.checkpoints.map((checkpoint) => <li key={checkpoint.id}><span>{checkpoint.sequence}</span><strong>{checkpoint.after_step_id ? task.plan.steps.find((step) => step.id === checkpoint.after_step_id)?.title ?? copy.checkpoint : copy.checkpoint}</strong><small>{new Date(checkpoint.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en")}</small></li>)}</ol>
              </section>
            )}
            <TaskEvidenceList evidence={task.evidence} />
          </div>
        )}
        {tab === "result" && <TaskResult result={task.result} completed={task.status === "completed"} />}
      </div>

      <footer className="task-canvas-footer">
        {task.pending_intervention && <span className="task-intervention-chip"><i />{task.pending_intervention.message}</span>}
        <TaskControls
          status={task.status}
          pendingCommands={session.pendingCommands}
          retryStepId={task.status === "waiting_for_user"
            ? (selectedStep?.status === "failed" ? selectedStep.id : task.plan.steps.find((step) => step.status === "failed")?.id)
            : null}
          onCommand={onCommand}
        />
      </footer>
    </section>
  );
}
