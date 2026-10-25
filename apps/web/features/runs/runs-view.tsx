"use client";

import type { ExecutionEvent, Run } from "@alcuin/contracts";
import { ArrowDownToLine, ArrowLeft, ArrowUpRight, Check, Clock3, FileText, MessageCircle, RefreshCw, Search, ShieldCheck, Wrench } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { alcuinApi } from "@/shared/lib/api";
import { StatusPill } from "@/shared/components/ui";
import { useI18n } from "@/shared/lib/i18n";
import { CitationResponse, collectCitationSources } from "@/features/studio/citations";
import { buildRunReceipt, collectReceiptArtifacts, exportRunReceiptMarkdown, filterRuns, receiptEvents, RUN_FILTERS, runDurationMs, type RunFilter } from "./run-receipt-model";
import { receiptCopy } from "./run-receipt-copy";
import "./run-receipts.css";

gsap.registerPlugin(useGSAP);
type Snapshot = Run & { events: ExecutionEvent[] };
type Detail = { id: string | null; status: "loading" | "ready" | "error"; snapshot?: Snapshot };

export function RunsView({ runs }: { runs: Run[] }) {
  const { locale, t } = useI18n();
  const copy = receiptCopy(locale);
  const [records, setRecords] = useState(runs);
  const [selected, setSelected] = useState<string | null>(runs[0]?.id ?? null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<RunFilter>("all");
  const [detail, setDetail] = useState<Detail>({ id: null, status: "loading" });
  const [revision, setRevision] = useState(0);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [listError, setListError] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  const refreshRef = useRef<AbortController | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const visible = useMemo(() => filterRuns(records, query, filter), [records, query, filter]);
  const activeRecord = visible.find((run) => run.id === selected);
  const runId = activeRecord?.id;
  const workspaceId = activeRecord?.workspace_id;
  const threadId = activeRecord?.thread_id;
  const ready = detail.id === runId && detail.status === "ready";
  const snapshot = ready ? detail.snapshot : undefined;
  const active = useMemo(() => snapshot && activeRecord ? { ...activeRecord, ...snapshot } : activeRecord, [activeRecord, snapshot]);
  const events = useMemo(() => snapshot ? receiptEvents(snapshot.id, snapshot.events).filter((event) => !Object.hasOwn(event, "workspace_id") || (event as ExecutionEvent & { workspace_id?: unknown }).workspace_id === snapshot.workspace_id) : [], [snapshot]);
  const receipt = useMemo(() => active && ready ? buildRunReceipt(active, events) : null, [active, events, ready]);
  const artifacts = useMemo(() => active && ready ? collectReceiptArtifacts(active, events) : [], [active, events, ready]);
  const sourceCount = useMemo(() => collectCitationSources(events).length, [events]);
  const answer = events.filter((event) => event.type === "message.delta" && typeof event.payload.delta === "string").map((event) => event.payload.delta as string).join("");
  const isTerminal = active && ["completed", "failed", "cancelled"].includes(active.status);
  const duration = active ? runDurationMs(active) : null;

  useEffect(() => {
    if (!runId || !workspaceId || !threadId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      setDetail({ id: runId, status: "error" });
    }, 10_000);
    void alcuinApi.getRun(runId, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result.id !== runId || result.workspace_id !== workspaceId || result.thread_id !== threadId) throw new Error("Mismatched record");
      setDetail({ id: runId, status: "ready", snapshot: result });
      setRecords((current) => current.map((item) => item.id === runId ? { ...item, ...result } : item));
    }).catch(() => {
      if (!controller.signal.aborted) setDetail({ id: runId, status: "error" });
    }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [runId, workspaceId, threadId, revision]);

  useEffect(() => () => refreshRef.current?.abort(), []);
  useGSAP(() => {
    if (!ready) return;
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo(".activity-detail-content", { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: .18, ease: "power2.out", clearProps: "opacity,visibility,transform" });
    }, rootRef);
    return () => mm.revert();
  }, { scope: rootRef, dependencies: [runId, ready, mobileDetail], revertOnUpdate: true });
  useEffect(() => {
    if (!mobileDetail || !window.matchMedia("(max-width: 900px)").matches) return;
    const frame = requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [mobileDetail, runId]);

  const refresh = async () => {
    if (refreshRef.current) return;
    const controller = new AbortController();
    refreshRef.current = controller;
    setRefreshing(true);
    setListError(false);
    const timer = setTimeout(() => {
      if (controller.signal.aborted) return;
      controller.abort();
      setListError(true);
      setRefreshing(false);
      refreshRef.current = null;
    }, 10_000);
    try {
      const result = await alcuinApi.listRuns(controller.signal);
      if (controller.signal.aborted) return;
      setRecords(result);
      setSelected((current) => result.some((run) => run.id === current) ? current : result[0]?.id ?? null);
      setDetail({ id: null, status: "loading" });
      setRevision((value) => value + 1);
    } catch { if (!controller.signal.aborted) setListError(true); }
    finally {
      clearTimeout(timer);
      if (!controller.signal.aborted) setRefreshing(false);
      if (refreshRef.current === controller) refreshRef.current = null;
    }
  };
  const select = (id: string) => {
    setSelected(id);
    setMobileDetail(true);
    setDownloadError(false);
  };
  const back = () => {
    setMobileDetail(false);
    requestAnimationFrame(() => rootRef.current?.querySelector<HTMLButtonElement>(`.activity-record[data-run-id="${CSS.escape(selected ?? "")}"]`)?.focus({ preventScroll: true }));
  };
  const download = () => {
    if (!active || !ready) return;
    setDownloadError(false);
    try {
      const url = URL.createObjectURL(new Blob([exportRunReceiptMarkdown(active, events, copy)], { type: "text/markdown;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `alcuin-receipt-${active.id.replace(/[^a-z0-9_-]/gi, "")}.md`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch { setDownloadError(true); }
  };

  return <div className="activity-surface" ref={rootRef} data-detail-open={mobileDetail}>
    <header className="activity-header"><div><p className="eyebrow">{copy("Your work, with a record")}</p><h1>{t("Runs")}</h1><p>{copy("See what happened, check the evidence, and pick up the conversation.")}</p></div><button className="button secondary" onClick={() => void refresh()} disabled={refreshing} aria-label={copy("Refresh records")}><RefreshCw size={15} className={refreshing ? "spin" : undefined} />{copy(refreshing ? "Refreshing…" : "Refresh records")}</button></header>
    {listError && <p className="activity-error" role="alert">{copy("Could not refresh records. Your previous list is still here.")}</p>}
    <div className="activity-body">
      <section className="activity-list" aria-label={copy("Recent records")}>
        <div className="activity-controls">
          <label className="activity-search"><Search size={16} /><input type="search" aria-label={copy("Search recent work")} placeholder={copy("Search recent work")} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <div className="activity-filter"><select aria-label={copy("Filter records")} value={filter} onChange={(event) => setFilter(event.target.value as RunFilter)}>{RUN_FILTERS.map((value) => <option value={value} key={value}>{copy(value)}</option>)}</select><span>{visible.length} / {records.length}</span></div>
          <p>{copy("Latest 30 runs · search and filters apply to this list only.")}</p>
        </div>
        <div className="activity-records">
          {visible.map((run) => <button className="activity-record" key={run.id} data-run-id={run.id} aria-pressed={selected === run.id} onClick={() => select(run.id)}>
            <span className="activity-record-top"><StatusPill status={run.status} /><time dateTime={run.created_at}>{dateText(run.created_at, locale, true)}</time></span>
            <strong>{run.input || run.title || copy("Untitled work")}</strong><span className="activity-record-meta">{run.agent_name || copy("Agent")}{run.title && <> · {run.title}</>}</span>
          </button>)}
          {visible.length === 0 && <div className="activity-empty"><FileText size={24} /><h2>{copy(records.length ? "No matching records" : "Your work will appear here")}</h2><p>{copy(records.length ? "Try another search or clear the filters." : "Start a conversation. Its results and evidence will stay together here.")}</p>{records.length ? <button className="button secondary" onClick={() => { setQuery(""); setFilter("all"); }}>{copy("Clear filters")}</button> : <Link className="button secondary" href="/studio?thread=new">{copy("Start a conversation")}</Link>}</div>}
        </div>
      </section>
      <section className="activity-detail" aria-label={copy("Work details")}>
        <button className="activity-back" onClick={back}><ArrowLeft size={16} />{copy("Back to records")}</button>
        {!active ? <div className="activity-empty"><Clock3 size={24} /><h2>{copy("Choose a record")}</h2><p>{copy("Results, decisions and sources will appear here.")}</p></div> : <>
          <header className="activity-detail-header"><StatusPill status={active.status} /><h2 tabIndex={-1} ref={headingRef}>{active.input || active.title || copy("Untitled work")}</h2><p>{active.agent_name || copy("Agent")} · {dateText(active.created_at, locale)}</p><div className="activity-actions"><Link className="button secondary" href={`/studio?thread=${encodeURIComponent(active.thread_id)}`}><MessageCircle size={14} />{copy("Open conversation")}<ArrowUpRight size={13} /></Link><button className="button secondary" onClick={download} disabled={!ready}><ArrowDownToLine size={14} />{copy("Download receipt")}</button></div>{downloadError && <p className="activity-error" role="alert">{copy("Could not download the receipt. Try again.")}</p>}</header>
          {!ready ? detail.id === runId && detail.status === "error" ? <div className="activity-empty" role="alert"><p>{copy("Could not load this record.")}</p><button className="button secondary" onClick={() => { setDetail({ id: null, status: "loading" }); setRevision((value) => value + 1); }}>{copy("Try again")}</button></div> : <div className="activity-loading" role="status">{copy("Loading this record…")}</div> : <div className="activity-detail-content">
            <dl className="activity-facts"><div><dt>{copy("Total duration")}</dt><dd>{duration === null ? "—" : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(duration / 1000)} ${copy("s")}`}</dd></div><div><dt>{copy("Tool calls")}</dt><dd>{receipt?.tools.length ?? 0}</dd></div><div><dt>{copy("Sources")}</dt><dd>{sourceCount}</dd></div></dl>
            {!isTerminal && <p className="activity-notice">{copy("This work is still open. Refresh for the latest status, or continue in its conversation.")}</p>}
            {active.status === "cancelled" && <p className="activity-notice">{copy("Stopped output is incomplete. Check any actions already started before retrying.")}</p>}
            {active.status === "failed" && <p className="activity-notice">{copy("This work did not finish. Any output below is partial; check action results before trying again.")}</p>}
            <section className="activity-section"><h3>{copy(active.status === "completed" ? "Result" : "Output so far")}</h3>{answer ? <CitationResponse key={active.id} content={answer} events={events} runId={active.id} /> : <><p className="activity-muted">{copy("No answer text was saved for this run.")}</p><CitationResponse key={active.id} content="" events={events} runId={active.id} /></>}</section>
            {artifacts.length > 0 && <section className="activity-section"><h3>{copy("Deliverables")}</h3>{artifacts.map((artifact) => <div className="activity-deliverable" key={artifact.id}><FileText size={17} /><span>{artifact.title}</span></div>)}<Link className="activity-text-link" href={`/studio?thread=${encodeURIComponent(active.thread_id)}`}>{copy("Open the conversation to view or edit deliverables")}<ArrowUpRight size={14} /></Link></section>}
            {Boolean(receipt?.tools.length) && <section className="activity-section"><h3>{copy("Action outcomes")}</h3><p className="activity-muted">{copy("Permission to act and the result of an action are different records.")}</p><ul className="activity-outcomes">{receipt?.tools.map((tool) => <li key={tool.callId}><Wrench size={15} /><span><strong>{tool.name}</strong><small>{copy(tool.status)}{tool.permission && <> · {copy("permission")}: {copy(tool.permission)}</>}</small></span></li>)}</ul></section>}
            <section className="activity-section"><h3>{copy("What happened")}</h3><ol className="activity-timeline">{receipt?.items.map((item) => <li key={item.id} data-kind={item.kind}><span className="activity-step-icon">{item.kind === "approval" ? <ShieldCheck size={14} /> : item.kind === "tool" ? <Wrench size={14} /> : item.kind === "input" ? <MessageCircle size={14} /> : item.status === "succeeded" ? <Check size={14} /> : <Clock3 size={14} />}</span><div><strong>{copy(item.label)}</strong>{item.name && <p>{item.name}</p>}<small>{copy(item.status)} · {dateText(item.timestamp, locale, true)}</small></div></li>)}</ol>{receipt?.items.length === 0 && <p className="activity-muted">{copy("No activity details were saved.")}</p>}</section>
            <footer className="activity-receipt-note"><p>{copy("The download includes status, action outcomes and source references, not private reasoning or raw tool inputs.")}</p><code>{active.id}</code></footer>
          </div>}
        </>}
      </section>
    </div>
  </div>;
}

function dateText(value: string, locale: string, compact = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", ...(compact ? {} : { year: "numeric" }) }).format(date);
}
