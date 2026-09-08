"use client";

import { ArrowDown, ArrowUp, GripVertical, LoaderCircle, Plus, Save, Trash2 } from "lucide-react";

import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";
import type { TaskPlanDraft } from "./task-session-reducer";

export function TaskPlanEditor({
  draft,
  busy = false,
  onChange,
  onSave,
  onCancel,
}: {
  draft: TaskPlanDraft;
  busy?: boolean;
  onChange: (draft: TaskPlanDraft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  const valid = draft.goal.trim().length > 0 && draft.steps.length > 0 && draft.steps.every((step) => step.title.trim().length > 0);

  const updateStep = (index: number, patch: Partial<TaskPlanDraft["steps"][number]>) => {
    const steps = draft.steps.map((step, stepIndex) => stepIndex === index ? { ...step, ...patch } : step);
    onChange({ ...draft, steps: normalizeOrdinals(steps) });
  };
  const moveStep = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (target < 0 || target >= draft.steps.length) return;
    const steps = [...draft.steps];
    [steps[index], steps[target]] = [steps[target], steps[index]];
    onChange({ ...draft, steps: normalizeOrdinals(steps) });
  };
  const removeStep = (index: number) => {
    onChange({ ...draft, steps: normalizeOrdinals(draft.steps.filter((_, stepIndex) => stepIndex !== index)) });
  };
  const addStep = () => {
    const id = typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `draft:${crypto.randomUUID()}`
      : `draft:${Date.now()}:${draft.steps.length}`;
    onChange({
      ...draft,
      steps: [...draft.steps, { id, title: "", description: "", ordinal: draft.steps.length }],
    });
  };

  return (
    <form className="task-plan-editor" onSubmit={(event) => { event.preventDefault(); if (valid && !busy) onSave(); }}>
      <header><div><small>{copy.plan}</small><h3>{copy.editPlan}</h3></div><p>{copy.editHint}</p></header>
      <label className="task-plan-goal">
        <span>{copy.task}</span>
        <textarea rows={2} value={draft.goal} disabled={busy} onChange={(event) => onChange({ ...draft, goal: event.target.value })} />
      </label>
      <ol className="task-plan-draft-list">
        {draft.steps.map((step, index) => (
          <li className="task-plan-draft-row" key={step.id}>
            <span className="task-plan-drag-handle" aria-hidden><GripVertical size={14} /></span>
            <span className="task-plan-draft-number">{String(index + 1).padStart(2, "0")}</span>
            <span className="task-plan-draft-fields">
              <input
                aria-label={`${copy.stepTitle} ${index + 1}`}
                value={step.title}
                disabled={busy}
                placeholder={copy.stepTitle}
                onChange={(event) => updateStep(index, { title: event.target.value })}
              />
              <textarea
                aria-label={`${copy.stepDescription} ${index + 1}`}
                rows={2}
                value={step.description}
                disabled={busy}
                placeholder={copy.stepDescription}
                onChange={(event) => updateStep(index, { description: event.target.value })}
              />
            </span>
            <span className="task-plan-draft-actions">
              <button type="button" onClick={() => moveStep(index, -1)} disabled={busy || index === 0} aria-label={`${copy.moveUp}: ${step.title}`}><ArrowUp size={12} /></button>
              <button type="button" onClick={() => moveStep(index, 1)} disabled={busy || index === draft.steps.length - 1} aria-label={`${copy.moveDown}: ${step.title}`}><ArrowDown size={12} /></button>
              <button type="button" className="danger" onClick={() => removeStep(index)} disabled={busy || draft.steps.length === 1} aria-label={`${copy.removeStep}: ${step.title}`}><Trash2 size={12} /></button>
            </span>
          </li>
        ))}
      </ol>
      <button type="button" className="task-add-step" onClick={addStep} disabled={busy}><Plus size={13} />{copy.addStep}</button>
      <footer>
        <button type="button" className="task-editor-cancel" onClick={onCancel} disabled={busy}>{copy.cancel}</button>
        <button type="submit" className="task-editor-save" disabled={busy || !valid}>
          {busy ? <LoaderCircle className="task-control-spinner" size={13} /> : <Save size={13} />}{copy.savePlan}
        </button>
      </footer>
    </form>
  );
}

function normalizeOrdinals(steps: TaskPlanDraft["steps"]): TaskPlanDraft["steps"] {
  return steps.map((step, index) => ({ ...step, ordinal: index }));
}
