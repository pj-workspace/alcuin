"use client";

import { Clock3, RotateCcw } from "lucide-react";
import type { TaskStep } from "@alcuin/contracts";

import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";
import { TaskEvidenceList } from "./task-evidence-list";

export function TaskStepDetail({ step }: { step: TaskStep | null }) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  if (!step) return null;

  return (
    <section className="task-step-detail" data-step-status={step.status}>
      <header>
        <div><small>{copy.stepDetails}</small><h3>{step.title}</h3></div>
        {step.attempts.length > 0 && <span className="task-attempt-count"><RotateCcw size={11} />{copy.attempts} · {step.attempts.length}</span>}
      </header>
      {step.description && <p className="task-step-description">{step.description}</p>}
      {step.attempts.length > 0 && (
        <ol className="task-attempt-list" aria-label={copy.attempts}>
          {step.attempts.map((attempt) => (
            <li data-attempt-status={attempt.status} key={attempt.id}>
              <span><Clock3 size={11} /></span>
              <strong>#{attempt.number}</strong>
              <small>{attempt.status.replaceAll("_", " ")}</small>
              {attempt.error && <p>{attempt.error}</p>}
            </li>
          ))}
        </ol>
      )}
      <div className="task-detail-evidence">
        <h4>{copy.evidence}</h4>
        <TaskEvidenceList evidence={step.evidence} />
      </div>
    </section>
  );
}
