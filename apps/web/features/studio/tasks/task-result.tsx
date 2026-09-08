"use client";

import { CheckCircle2, FileOutput, ListChecks } from "lucide-react";
import type { Task } from "@alcuin/contracts";

import { MarkdownContent } from "@/shared/components/markdown-content";
import { useI18n } from "@/shared/lib/i18n";
import { taskCopy } from "./task-copy";

export function TaskResult({ result, completed = false }: { result?: Task["result"]; completed?: boolean }) {
  const { locale } = useI18n();
  const copy = taskCopy(locale);
  const display = normalizeResult(result);

  if (!display) {
    return (
      <div className="task-result-empty">
        <FileOutput size={17} />
        <p>{copy.noResult}</p>
      </div>
    );
  }

  return (
    <article className="task-result" data-completed={completed || undefined}>
      <header>
        <span>{completed ? <CheckCircle2 size={15} /> : <FileOutput size={15} />}</span>
        <div><small>{copy.result}</small>{display.title && <h3>{display.title}</h3>}</div>
      </header>
      {display.contentType === "application/json"
        ? <pre>{display.content}</pre>
        : display.contentType === "text/plain"
          ? <p className="task-result-plain">{display.content}</p>
          : <MarkdownContent content={display.content} variant="artifact" />}
      {display.stepCount > 0 && (
        <footer className="task-result-meta">
          <ListChecks size={12} />
          <span>{display.stepCount} {locale === "zh" ? "个步骤已形成检查点" : `checkpointed step${display.stepCount === 1 ? "" : "s"}`}</span>
        </footer>
      )}
    </article>
  );
}

function normalizeResult(result: Task["result"]): { title?: string; content: string; contentType: string; stepCount: number } | null {
  if (!result) return null;
  const title = typeof result.title === "string" ? result.title : undefined;
  const stepCount = Array.isArray(result.completed_step_ids)
    ? result.completed_step_ids.length
    : Array.isArray(result.steps) ? result.steps.length : 0;
  if (typeof result.content === "string") {
    return {
      title,
      content: result.content,
      contentType: typeof result.content_type === "string" ? result.content_type : "text/markdown",
      stepCount,
    };
  }
  if (typeof result.summary === "string" && result.summary.trim()) {
    return {
      title,
      content: result.summary,
      contentType: "text/markdown",
      stepCount,
    };
  }
  return {
    title,
    content: JSON.stringify(result, null, 2),
    contentType: "application/json",
    stepCount,
  };
}
