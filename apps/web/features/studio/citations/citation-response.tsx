"use client";

import type { ExecutionEvent } from "@alcuin/contracts";
import { BookOpen, ChevronDown, Copy as CopyIcon, ExternalLink, FileText, Globe2, Link2, X } from "lucide-react";
import { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { defaultUrlTransform } from "react-markdown";

import { MarkdownContent } from "@/shared/components/markdown-content";
import { useI18n } from "@/shared/lib/i18n";
import { hasCitationReferences } from "./citation-cache";
import { loadRunCitations } from "./citation-access";
import { portableRunMarkdown } from "./portable-run-markdown";
import { citationCopy } from "./citation-copy";
import { citationIdFromHref, createCitationSourceSelector, remarkCitationMarkers, resolveCitationSource, type CitationSource } from "./citation-model";
import "./citation-evidence.css";

type Copy = ReturnType<typeof citationCopy>;
type Preview = { source: CitationSource | null; href: string; anchor: HTMLElement; pinned: boolean };
type HistoryStatus = "idle" | "loading" | "ready" | "error";
const citationPlugins = [remarkCitationMarkers];
const noEvents: ExecutionEvent[] = [];
const citationUrlTransform = (url: string) => citationIdFromHref(url) || url.startsWith("knowledge://") ? url : defaultUrlTransform(url);

export const CitationResponse = memo(function CitationResponse({ content, events, runId, running = false, variant = "answer", onCopy }: { content: string; events: ExecutionEvent[]; runId?: string | null; running?: boolean; variant?: "answer" | "thinking" | "artifact"; onCopy?: (content: string) => void | Promise<void> }) {
  const { locale, t } = useI18n();
  const copy = useMemo(() => citationCopy(locale), [locale]);
  const [history, setHistory] = useState<{ runId?: string | null; status: HistoryStatus; events: ExecutionEvent[] }>({ status: "idle", events: [] });
  const historyStatus = history.runId === runId ? history.status : "idle";
  const historyPending = Boolean(runId && !running && historyStatus !== "ready");
  const selectSources = useMemo(() => createCitationSourceSelector(), []);
  const sources = useMemo(() => selectSources(events, history.runId === runId ? history.events : noEvents), [events, history.events, history.runId, runId, selectSources]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [copying, setCopying] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewId = useId();

  const cancelClose = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);
  const dismiss = useCallback((restoreFocus = false) => {
    cancelClose();
    setPreview((current) => {
      if (restoreFocus) current?.anchor.focus({ preventScroll: true });
      return null;
    });
  }, [cancelClose]);
  const scheduleClose = useCallback(() => {
    cancelClose();
    closeTimer.current = setTimeout(() => setPreview((current) => current?.pinned ? current : null), 180);
  }, [cancelClose]);
  useEffect(() => () => cancelClose(), [cancelClose]);

  const loadHistory = useCallback(() => {
    if (!runId || running || historyStatus === "loading") return;
    setHistory({ runId, status: "loading", events: [] });
    void loadRunCitations(runId).then(
      (loaded) => setHistory({ runId, status: "ready", events: loaded }),
      () => setHistory({ runId, status: "error", events: [] }),
    );
  }, [runId, running, historyStatus]);

  const copyResponse = async () => {
    if (!onCopy || copying) return;
    setCopying(true);
    setCopyFailed(false);
    try { await onCopy(await portableRunMarkdown(content, [...events, ...history.events], runId, locale)); }
    catch { setCopyFailed(true); }
    finally { setCopying(false); }
  };

  const showPreview = useCallback((source: CitationSource | null, href: string, anchor: HTMLElement, pinned: boolean) => {
    cancelClose();
    setPreview((current) => !pinned && current?.pinned ? current : { source, href, anchor, pinned });
    if (pinned && !source) loadHistory();
  }, [cancelClose, loadHistory]);

  const previewHref = preview?.href;
  const renderLink = useCallback(({ href = "", children }: { href?: string; children: ReactNode }) => {
    const source = resolveCitationSource(href, sources);
    const explicit = citationIdFromHref(href);
    if (!source && !explicit && !href.startsWith("knowledge://")) return undefined;
    return <button
      type="button"
      className={`citation-inline ${source || historyPending ? "" : "citation-unmatched"}`}
      data-citation-number={source?.number}
      aria-label={source ? `${copy.open} ${source.number}: ${source.title || copy.unknownTitle}` : historyPending ? copy.loadSources : copy.missing}
      aria-haspopup="dialog"
      aria-controls={previewHref === href ? previewId : undefined}
      data-preview-id={`${previewId}-${href}`}
      onPointerEnter={(event) => { if (event.pointerType !== "touch") showPreview(source, href, event.currentTarget, false); }}
      onPointerLeave={scheduleClose}
      onClick={(event) => showPreview(source, href, event.currentTarget, true)}
    >
      {!explicit && <span className="citation-link-label">{children}</span>}
      <span className="citation-reference-number">{source?.number ?? "?"}</span>
    </button>;
  }, [sources, historyPending, copy, previewHref, previewId, showPreview, scheduleClose]);

  return <div className="citation-response">
    {content && <MarkdownContent
      content={content}
      variant={variant}
      remarkPlugins={citationPlugins}
      urlTransform={citationUrlTransform}
      renderLink={renderLink}
    />}
    {sources.length > 0 && <CitationSourceList sources={sources} copy={copy} locale={locale} defaultExpanded={historyStatus === "ready"} />}
    {sources.length === 0 && runId && !running && hasCitationReferences(content) && <div className="citation-history">
      {historyStatus !== "ready" && <button className="citation-sources-toggle" disabled={historyStatus === "loading"} onClick={loadHistory}><BookOpen size={13} />{historyStatus === "loading" ? copy.loadingSources : historyStatus === "error" ? copy.retry : copy.loadSources}</button>}
      {historyStatus === "ready" && <p>{copy.noSources}</p>}
      {historyStatus === "error" && <p role="status">{copy.sourcesError}</p>}
    </div>}
    {onCopy && content && !running && <div className="assistant-actions"><button onClick={copyResponse} disabled={copying} aria-label={t("Copy response")}><CopyIcon size={13} />{copying ? copy.copying : t("Copy")}</button>{copyFailed && <span role="status">{copy.copyFailed}</span>}</div>}
    {preview && <CitationPreview
      id={previewId}
      preview={{ ...preview, source: resolveCitationSource(preview.href, sources) ?? preview.source }}
      copy={copy}
      locale={locale}
      onClose={dismiss}
      onPointerEnter={cancelClose}
      onPointerLeave={scheduleClose}
      historyStatus={historyStatus}
      historyPending={historyPending}
      onRetry={loadHistory}
    />}
  </div>;
});

export const CitationSourceList = memo(function CitationSourceList({ sources, copy, locale, defaultExpanded = false }: { sources: CitationSource[]; copy: Copy; locale: "en" | "zh"; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [selected, setSelected] = useState<string | null>(null);
  const listId = useId();
  return <section className="citation-sources" aria-label={copy.sources}>
    <button className="citation-sources-toggle" aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(!expanded)}>
      <span className="citation-source-icons" aria-hidden="true">
        {sources.slice(0, 3).map((source) => <span key={source.key}><SourceIcon kind={source.kind} /></span>)}
      </span>
      <span>{copy.sources}</span><span className="citation-count">{sources.length}</span>
      <ChevronDown size={13} className={expanded ? "expanded" : ""} />
    </button>
    <div className="citation-collapse" data-expanded={expanded} aria-hidden={!expanded} inert={!expanded} id={listId}>
      <div className="citation-collapse-inner">
        <div className="citation-source-body">
          <p className="citation-source-note">{copy.note}</p>
          <ol className="citation-source-list">
            {sources.map((source) => {
              const open = selected === source.key;
              const detailId = `${listId}-${source.number}`;
              return <li key={source.key}>
                <button className="citation-source-row" aria-expanded={open} aria-controls={detailId} onClick={() => setSelected(open ? null : source.key)}>
                  <span className="citation-list-number">{source.number}</span>
                  <span className="citation-source-row-text"><strong>{source.title || copy.unknownTitle}</strong><small>{source.publisher || sourceKind(source, copy)}{source.kind === "knowledge" && source.chunkIndex !== null ? ` · ${copy.chunk} ${source.chunkIndex + 1}` : ""}</small></span>
                  <ChevronDown size={14} className={open ? "expanded" : ""} />
                </button>
                <div className="citation-collapse" data-expanded={open} aria-hidden={!open} inert={!open} id={detailId}>
                  <div className="citation-collapse-inner"><div className="citation-source-details"><SourceEvidence source={source} copy={copy} locale={locale} /></div></div>
                </div>
              </li>;
            })}
          </ol>
        </div>
      </div>
    </div>
  </section>;
});

function CitationPreview({ id, preview, copy, locale, onClose, onPointerEnter, onPointerLeave, historyStatus, historyPending, onRetry }: {
  id: string;
  preview: Preview;
  copy: Copy;
  locale: "en" | "zh";
  onClose: (restoreFocus?: boolean) => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  historyStatus: HistoryStatus;
  historyPending: boolean;
  onRetry: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 12, width: 360 });

  useLayoutEffect(() => {
    let frame = 0;
    const measure = () => {
      if (!preview.anchor.isConnected) { onClose(); return; }
      const anchor = preview.anchor.getBoundingClientRect();
      if (!anchor.width && !anchor.height) { onClose(); return; }
      const margin = 12;
      const width = Math.min(376, window.innerWidth - margin * 2);
      const height = Math.min(ref.current?.offsetHeight ?? 300, window.innerHeight - margin * 2);
      const below = anchor.bottom + 8;
      const top = below + height <= window.innerHeight - margin
        ? below : Math.max(margin, anchor.top - height - 8);
      setPosition({ left: Math.max(margin, Math.min(anchor.left, window.innerWidth - width - margin)), top, width });
    };
    const scheduleMeasure = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(measure);
    };
    measure();
    const resizeObserver = new ResizeObserver(scheduleMeasure);
    resizeObserver.observe(preview.anchor);
    if (ref.current) resizeObserver.observe(ref.current);
    void document.fonts?.ready.then(scheduleMeasure);
    window.addEventListener("resize", scheduleMeasure);
    window.addEventListener("scroll", scheduleMeasure, true);
    return () => {
      window.cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      window.removeEventListener("resize", scheduleMeasure);
      window.removeEventListener("scroll", scheduleMeasure, true);
    };
  }, [preview.anchor, preview.source, onClose]);

  useEffect(() => {
    if (preview.pinned) closeRef.current?.focus({ preventScroll: true });
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onClose(preview.pinned); } };
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !ref.current?.contains(event.target) && !preview.anchor.contains(event.target)) onClose();
    };
    document.addEventListener("keydown", key);
    document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("pointerdown", outside); };
  }, [preview.anchor, preview.pinned, onClose]);

  return createPortal(<div
    ref={ref}
    id={id}
    role="dialog"
    aria-modal="false"
    aria-label={preview.source ? `${copy.open} ${preview.source.number}` : historyPending ? copy.loadSources : copy.missing}
    className="citation-preview"
    style={position}
    onPointerEnter={onPointerEnter}
    onPointerLeave={onPointerLeave}
  >
    <header className="citation-preview-header">
      <span className="citation-preview-kind"><SourceIcon kind={preview.source?.kind ?? "other"} />{preview.source ? `${sourceKind(preview.source, copy)} · ${preview.source.number}` : historyPending ? copy.loadSources : copy.missing}</span>
      <button ref={closeRef} onClick={() => onClose(true)} aria-label={copy.close}><X size={15} /></button>
    </header>
    {preview.source ? <>
      <h3>{preview.source.title || copy.unknownTitle}</h3>
      <SourceEvidence source={preview.source} copy={copy} locale={locale} />
    </> : <div className="citation-missing-message" role="status"><p>{historyStatus === "loading" ? copy.loadingSources : historyStatus === "error" ? copy.sourcesError : historyPending ? copy.pendingSource : copy.missingDetail}</p>{historyStatus === "error" && <button className="citation-sources-toggle" onClick={onRetry}>{copy.retry}</button>}</div>}
  </div>, document.body);
}

function SourceEvidence({ source, copy, locale }: { source: CitationSource; copy: Copy; locale: "en" | "zh" }) {
  const retrieved = Number.isNaN(Date.parse(source.retrievedAt)) ? null : new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(source.retrievedAt));
  return <>
    <div className="citation-evidence-meta">
      {source.documentId && <span data-document-id={source.documentId}><FileText size={12} />{copy.document} · {source.title || copy.unknownTitle}</span>}
      {source.pageNumber !== null && <span>{copy.page} {source.pageNumber}</span>}
      {source.chunkIndex !== null && <span>{copy.chunk} {source.chunkIndex + 1}</span>}
    </div>
    <small className="citation-excerpt-label">{copy.excerpt}</small>
    {source.snippet ? <blockquote className="citation-excerpt">{source.snippet}</blockquote> : <p className="citation-no-excerpt">{copy.noExcerpt}</p>}
    <p className="citation-excerpt-note">{copy.excerptNote}</p>
    <footer className="citation-evidence-footer">
      {source.url ? <a href={source.url} target="_blank" rel="noreferrer noopener"><ExternalLink size={12} />{copy.original}</a> : <span><Link2 size={12} />{copy.unavailable}</span>}
      {retrieved && <time dateTime={source.retrievedAt} title={`${copy.retrievedAt}: ${retrieved}`}>{retrieved}</time>}
    </footer>
  </>;
}

function SourceIcon({ kind }: { kind: CitationSource["kind"] }) {
  return kind === "knowledge" ? <BookOpen size={13} /> : kind === "web" ? <Globe2 size={13} /> : <FileText size={13} />;
}

function sourceKind(source: CitationSource, copy: Copy) {
  return source.kind === "knowledge" ? copy.knowledge : source.kind === "web" ? copy.web : copy.other;
}
