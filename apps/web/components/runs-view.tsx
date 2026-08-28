"use client";

import type { ExecutionEvent, Run } from "@alcuin/contracts";
import { ArrowDownToLine, ChevronRight, Clock3, Filter, Gauge, Search, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/lib/api";
import { StatusPill } from "@/components/ui";

export function RunsView({ runs, events, onSelectEvents }: { runs: Run[]; events: ExecutionEvent[]; onSelectEvents: (events: ExecutionEvent[]) => void }) {
  const [selected, setSelected] = useState(runs[0]?.id);
  const [loading, setLoading] = useState(false);
  async function select(runId: string) {
    setSelected(runId); setLoading(true);
    try { onSelectEvents((await alcuinApi.getRun(runId)).events); } finally { setLoading(false); }
  }
  const activeRun = runs.find((run) => run.id === selected) ?? runs[0];
  return <div className="wide-surface runs-surface">
    <header className="wide-header"><div><div className="eyebrow">Execution observability</div><h1>Runs</h1><p>Every model delta, tool call, approval and artifact preserved in sequence.</p></div><div className="header-actions"><button className="button secondary"><ArrowDownToLine size={14} />Export trace</button></div></header>
    <div className="run-kpis"><div><span>Success rate</span><strong>98.4%</strong><small><i className="up" />2.1% this week</small></div><div><span>Median duration</span><strong>4.8s</strong><small>7-day window</small></div><div><span>Approval rate</span><strong>12%</strong><small>mutating operations</small></div><div><span>Token volume</span><strong>18.2k</strong><small>current workspace</small></div></div>
    <div className="runs-body">
      <section className="run-list-panel"><div className="table-toolbar"><div className="filter-input"><Search size={14} /><input placeholder="Search runs…" /></div><button className="button secondary"><Filter size={13} />Filter</button></div><div className="run-table-head"><span>Run</span><span>Status</span><span>Agent</span><span>Started</span><span /></div>{runs.map((run) => <button key={run.id} className={clsx("run-table-row", selected === run.id && "active")} onClick={() => void select(run.id)}><span><strong>{run.title ?? run.input}</strong><small>{run.id.slice(0, 18)}</small></span><StatusPill status={run.status} /><span>{run.agent_name ?? "Operations Copilot"}</span><span>{relativeTime(run.created_at)}</span><ChevronRight size={14} /></button>)}</section>
      <aside className="run-detail"><header><div><small>Selected run</small><h2>{activeRun?.title ?? "Run trace"}</h2></div>{activeRun && <StatusPill status={activeRun.status} />}</header><div className="run-summary"><div><Clock3 size={14} /><span>Duration<strong>1.4s</strong></span></div><div><Gauge size={14} /><span>Events<strong>{events.length}</strong></span></div><div><Sparkles size={14} /><span>Runtime<strong>ReAct</strong></span></div></div><div className={clsx("detail-timeline", loading && "is-loading")}>{events.map((event) => <div className="detail-event" key={event.id}><span className={clsx("detail-event-icon", event.type.includes("tool") && "tool", event.type.includes("approval") && "approval")}>{event.type.includes("tool") ? <Wrench size={12} /> : event.type.includes("approval") ? <ShieldCheck size={12} /> : <span />}</span><div><strong>{event.type}</strong><p>{String(event.payload.summary ?? event.payload.result_summary ?? event.payload.label ?? event.payload.status ?? "Event recorded")}</p><small>Sequence {event.sequence}</small></div></div>)}</div></aside>
    </div>
  </div>;
}

function relativeTime(value: string) {
  const difference = Date.now() - new Date(value).getTime();
  if (difference < 60_000) return "just now";
  if (difference < 3_600_000) return `${Math.floor(difference / 60_000)}m ago`;
  return `${Math.floor(difference / 3_600_000)}h ago`;
}
