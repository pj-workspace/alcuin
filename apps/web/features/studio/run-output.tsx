"use client";

import type { ExecutionEvent } from "@alcuin/contracts";
import { Check, ChevronRight, CircleAlert, Clock3, Database, Search, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { clsx } from "clsx";
import type { OrbState } from "thinking-orbs";

import { AgentPresenceOrb } from "@/features/studio/agent-presence-orb";
import { CitationResponse } from "@/features/studio/citations";
import { ThinkingMarkdown } from "@/shared/components/thinking-markdown";
import { useI18n } from "@/shared/lib/i18n";

type ReasoningTrace = {
  kind: "reasoning";
  key: string;
  sequence: number;
  content: string;
};

type ToolTrace = {
  kind: "tool";
  key: string;
  sequence: number;
  name: string;
  summary: string;
  query?: string;
  status: "running" | "success" | "error";
};

type TraceStep = ReasoningTrace | ToolTrace;
type Presence = { label: string; state: OrbState };

export function RunOutput({
  events,
  running,
  assistantText,
  onCopy,
  runId,
}: {
  events: ExecutionEvent[];
  running: boolean;
  assistantText: string;
  onCopy: (text: string) => void | Promise<void>;
  runId?: string | null;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const [tracePinned, setTracePinned] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const steps = useMemo(() => collectTraceSteps(events), [events]);
  const tools = steps.filter((step): step is ToolTrace => step.kind === "tool");
  const startedEvent = events.find((event) => event.type === "run.started");
  const startedAt = startedEvent?.timestamp;
  const completed = events.some((event) => event.type === "run.completed");
  const failed = events.some((event) => event.type === "run.failed");
  const hasTrace = Boolean(running || startedAt || steps.length || completed || failed);
  const runIdentity = startedEvent?.run_id ?? events[0]?.run_id ?? "empty";

  useEffect(() => {
    setCollapsed(true);
    setTracePinned(false);
  }, [runIdentity]);

  useEffect(() => {
    if (tracePinned) return;
    if (running && steps.length > 0 && !assistantText) {
      setCollapsed(false);
      return;
    }
    if (assistantText || !running) setCollapsed(true);
  }, [assistantText, running, steps.length, tracePinned]);

  useEffect(() => {
    if (!running || !startedAt) return;
    const update = () => setElapsed(Math.max(0, (Date.now() - Date.parse(startedAt)) / 1000));
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [running, startedAt]);

  const terminalAt = [...events]
    .reverse()
    .find((event) => event.type === "run.completed" || event.type === "run.failed")?.timestamp;
  const duration = startedAt
    ? terminalAt
      ? Math.max(0, (Date.parse(terminalAt) - Date.parse(startedAt)) / 1000)
      : elapsed
    : 0;
  const errorMessage = [...events]
    .reverse()
    .find((event) => event.type === "run.failed")?.payload.message;
  const presence = derivePresence(events, steps, running, assistantText, failed, duration, t);
  const runningTool = tools.find((tool) => tool.status === "running");
  const lastStep = steps.at(-1);

  return <>
    {hasTrace && (
      <section className={clsx("run-brainstorm", running && "streaming")}>
        <button
          className="brainstorm-toggle"
          onClick={() => { setTracePinned(true); setCollapsed((value) => !value); }}
          aria-expanded={!collapsed}
          aria-label={t(collapsed ? "Show run trace" : "Hide run trace")}
        >
          <AgentPresenceOrb state={presence.state} active={running} />
          <span className="brainstorm-label-shell">
            <span className="brainstorm-label" key={presence.label}>{presence.label}</span>
            {running && <i className="brainstorm-label-sweep" />}
          </span>
          <ChevronRight className={clsx("brainstorm-chevron", !collapsed && "expanded")} size={14} />
        </button>
        <div
          className={clsx("brainstorm-collapse", collapsed && "collapsed")}
          aria-hidden={collapsed}
        >
          <div className="brainstorm-collapse-inner">
            <div className="brainstorm-steps">
              {steps.map((step) => step.kind === "reasoning" ? (
                <div className="brainstorm-step reasoning-step" key={step.key}>
                  <span className={clsx(
                    "brainstorm-node",
                    running && lastStep?.key === step.key && !runningTool && !assistantText && "live",
                  )}>
                    <Clock3 size={11} />
                  </span>
                  <ThinkingMarkdown content={step.content} />
                </div>
              ) : (
                <div className="brainstorm-step tool-step" key={step.key}>
                  <span className={clsx("brainstorm-node", "tool", step.status)}>
                    <ToolNodeIcon step={step} />
                  </span>
                  <span className="tool-step-label">{toolStepLabel(step, t)}</span>
                </div>
              ))}
              {completed && steps.length > 0 && (
                <div className="brainstorm-step completion-step">
                  <span className="brainstorm-node success"><Check size={11} /></span>
                  <span>{t("Complete")}</span>
                </div>
              )}
              {failed && (
                <div className="brainstorm-step completion-step failed">
                  <span className="brainstorm-node error"><CircleAlert size={11} /></span>
                  <span>{t("Interrupted")}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    )}
    {(assistantText || events.some((event) => event.type === "citation.created")) && (
      <div className={clsx("assistant-output", running && "streaming")}>
        <CitationResponse content={assistantText} events={events} runId={runId ?? startedEvent?.run_id} running={running} onCopy={onCopy} />
      </div>
    )}
    {!assistantText && errorMessage && <div className="run-error"><CircleAlert size={14} /><span>{String(errorMessage)}</span></div>}
  </>;
}

function derivePresence(
  events: ExecutionEvent[],
  steps: TraceStep[],
  running: boolean,
  assistantText: string,
  failed: boolean,
  duration: number,
  t: ReturnType<typeof useI18n>["t"],
): Presence {
  if (!running) {
    return {
      label: failed ? t("Thought process interrupted") : t("Thought process · {duration}", { duration: formatDuration(duration) }),
      state: "breathing",
    };
  }

  const runningTool = steps.find((step): step is ToolTrace => step.kind === "tool" && step.status === "running");
  if (runningTool) return { label: toolStepLabel(runningTool, t), state: toolOrbState(runningTool.name) };
  if (assistantText) return { label: t("Composing response"), state: "composing" };

  const hasReasoning = events.some((event) => event.type === "reasoning.delta");
  const hasFinishedTool = steps.some((step) => step.kind === "tool" && step.status !== "running");
  if (hasReasoning) {
    return hasFinishedTool
      ? { label: t("Weaving evidence"), state: "weaving" }
      : { label: t("Thinking"), state: "breathing" };
  }
  return { label: t("Connecting"), state: "connecting" };
}

function collectTraceSteps(events: ExecutionEvent[]): TraceStep[] {
  const steps: TraceStep[] = [];
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (event.type === "reasoning.delta") {
      const delta = String(event.payload.delta ?? "");
      if (!delta) continue;
      const last = steps.at(-1);
      if (last?.kind === "reasoning") last.content += delta;
      else steps.push({ kind: "reasoning", key: event.id, sequence: event.sequence, content: delta });
    }
    if (event.type === "tool.requested") {
      const name = String(event.payload.tool ?? "tool");
      steps.push({
        kind: "tool",
        key: event.id,
        sequence: event.sequence,
        name,
        summary: String(event.payload.summary ?? "Tool requested"),
        query: extractToolQuery(event.payload.arguments),
        status: "running",
      });
    }
    if (event.type === "tool.completed") {
      const name = String(event.payload.tool ?? "tool");
      const target = [...steps]
        .reverse()
        .find((step): step is ToolTrace => step.kind === "tool" && step.name === name && step.status === "running");
      if (target) target.status = event.payload.status === "succeeded" ? "success" : "error";
    }
  }
  return steps;
}

function ToolNodeIcon({ step }: { step: ToolTrace }) {
  if (step.status === "running") return <span className="micro-loader" />;
  if (step.status === "error") return <CircleAlert size={11} />;
  if (isKnowledgeTool(step.name)) return <Database size={11} />;
  if (isSearchTool(step.name)) return <Search size={11} />;
  return <Wrench size={10} />;
}

function toolStepLabel(step: ToolTrace, t: ReturnType<typeof useI18n>["t"]): string {
  const query = step.query?.trim();
  const prefix = isKnowledgeTool(step.name) ? t("Retrieve") : isSearchTool(step.name) ? t("Search") : formatToolName(step.name);
  if (step.status === "error") return `${t("{prefix} interrupted", { prefix })}${query ? ` · ${query}` : ""}`;
  const summary = step.summary === "Tool requested" ? t("Tool requested") : step.summary;
  return `${prefix}${query ? ` · ${query}` : summary ? ` · ${summary}` : ""}`;
}

function extractToolQuery(argumentsValue: unknown): string | undefined {
  if (!argumentsValue || typeof argumentsValue !== "object") return undefined;
  const args = argumentsValue as Record<string, unknown>;
  for (const key of ["query", "q", "search_query", "term", "text"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return truncate(value.trim(), 64);
  }
  return undefined;
}

function toolOrbState(name: string): OrbState {
  if (isKnowledgeTool(name)) return "solving";
  if (isSearchTool(name)) return "searching";
  if (/artifact|form|schema|render|shape/i.test(name)) return "shaping";
  return "working";
}

function isKnowledgeTool(name: string): boolean {
  return /knowledge|retriev|vector|qdrant|kb[._/-]|[._/-]kb/i.test(name);
}

function isSearchTool(name: string): boolean {
  return /web|searx|search|query|lookup|检索|搜索/i.test(name);
}

function formatToolName(name: string): string {
  const leaf = name.split(/[./]/).at(-1) ?? name;
  return leaf.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return "<1s";
  if (seconds < 10) return `${Math.round(seconds * 10) / 10}s`;
  return `${Math.round(seconds)}s`;
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}
