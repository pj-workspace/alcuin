"use client";

import type { ExecutionEvent, Run } from "@alcuin/contracts";
import { ArrowDownToLine, ChevronRight, Clock3, Filter, Gauge, Search, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/lib/api";
import { StatusPill } from "@/components/ui";
import { useI18n } from "@/lib/i18n";

export function RunsView({ runs, events, onSelectEvents }: { runs: Run[]; events: ExecutionEvent[]; onSelectEvents: (events: ExecutionEvent[]) => void }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState(runs[0]?.id);
  const [loading, setLoading] = useState(false);
  async function select(runId: string) {
    setSelected(runId); setLoading(true);
    try { onSelectEvents((await alcuinApi.getRun(runId)).events); } finally { setLoading(false); }
  }
  const activeRun = runs.find((run) => run.id === selected) ?? runs[0];
  return <div className="wide-surface runs-surface">
    <header className="wide-header"><div><div className="eyebrow">{t("Execution observability")}</div><h1>{t("Runs")}</h1><p>{t("Every model delta, tool call, approval and artifact preserved in sequence.")}</p></div><div className="header-actions"><button className="button secondary"><ArrowDownToLine size={14} />{t("Export trace")}</button></div></header>
    <div className="run-kpis"><div><span>{t("Success rate")}</span><strong>98.4%</strong><small><i className="up" />2.1% {t("this week")}</small></div><div><span>{t("Median duration")}</span><strong>4.8s</strong><small>{t("7-day window")}</small></div><div><span>{t("Approval rate")}</span><strong>12%</strong><small>{t("mutating operations")}</small></div><div><span>{t("Token volume")}</span><strong>18.2k</strong><small>{t("current workspace")}</small></div></div>
    <div className="runs-body">
      <section className="run-list-panel"><div className="table-toolbar"><div className="filter-input"><Search size={14} /><input placeholder={t("Search runs…")} /></div><button className="button secondary"><Filter size={13} />{t("Filter")}</button></div><div className="run-table-head"><span>{t("Run")}</span><span>{t("Status")}</span><span>{t("Agent")}</span><span>{t("Started")}</span><span /></div>{runs.map((run) => <button key={run.id} className={clsx("run-table-row", selected === run.id && "active")} onClick={() => void select(run.id)}><span><strong>{run.title ?? run.input}</strong><small>{run.id.slice(0, 18)}</small></span><StatusPill status={run.status} /><span>{run.agent_name ?? "Alcuin Starter"}</span><span>{relativeTime(run.created_at, t)}</span><ChevronRight size={14} /></button>)}</section>
      <aside className="run-detail"><header><div><small>{t("Selected run")}</small><h2>{activeRun?.title ?? t("Run trace")}</h2></div>{activeRun && <StatusPill status={activeRun.status} />}</header><div className="run-summary"><div><Clock3 size={14} /><span>{t("Duration")}<strong>1.4s</strong></span></div><div><Gauge size={14} /><span>{t("Events")}<strong>{events.length}</strong></span></div><div><Sparkles size={14} /><span>{t("Runtime")}<strong>ReAct</strong></span></div></div><div className={clsx("detail-timeline", loading && "is-loading")}>{events.map((event) => <div className="detail-event" key={event.id}><span className={clsx("detail-event-icon", event.type.includes("tool") && "tool", event.type.includes("approval") && "approval")}>{event.type.includes("tool") ? <Wrench size={12} /> : event.type.includes("approval") ? <ShieldCheck size={12} /> : <span />}</span><div><strong>{event.type}</strong><p>{String(event.payload.summary ?? event.payload.result_summary ?? event.payload.label ?? event.payload.status ?? t("Event recorded"))}</p><small>{t("Sequence {sequence}", { sequence: event.sequence })}</small></div></div>)}</div></aside>
    </div>
  </div>;
}

function relativeTime(value: string, t: ReturnType<typeof useI18n>["t"]) {
  const difference = Date.now() - new Date(value).getTime();
  if (difference < 60_000) return t("just now");
  if (difference < 3_600_000) return t("{count}m ago", { count: Math.floor(difference / 60_000) });
  return t("{count}h ago", { count: Math.floor(difference / 3_600_000) });
}
