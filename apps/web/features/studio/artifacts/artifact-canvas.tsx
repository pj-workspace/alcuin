"use client";

import type { ArtifactResource, ExecutionEvent } from "@alcuin/contracts";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  Copy,
  Code2,
  Download,
  Edit3,
  FileText,
  LoaderCircle,
  RefreshCw,
  Save,
  Sparkles,
  X,
} from "lucide-react";
import { clsx } from "clsx";

import { AlcuinMark } from "@/shared/components/alcuin-mark";
import { CitationResponse, portableRunMarkdown } from "@/features/studio/citations";
import { useI18n } from "@/shared/lib/i18n";
import { alcuinApi } from "@/shared/lib/api";
import { artifactDownloadName } from "./artifact-html";
import { ArtifactHtmlPreview } from "./artifact-html-preview";
import { useArtifactWorkspace } from "./use-artifact-workspace";
import { citationsForArtifact } from "./artifact-citation-model";
import { artifactDocumentBody } from "./artifact-document-model";

export function ArtifactCanvas({
  enabled,
  threadId,
  eventArtifacts,
  citationEvents,
  activeRunId,
  running,
  onNotify,
}: {
  enabled: boolean;
  threadId: string | null;
  eventArtifacts: readonly unknown[];
  citationEvents: readonly ExecutionEvent[];
  activeRunId: string | null;
  running: boolean;
  onNotify: (message: string) => void;
}) {
  const { t, locale } = useI18n();
  const workspace = useArtifactWorkspace({ threadId, eventArtifacts, running });
  const { state, displayedArtifact } = workspace;
  const status = artifactStatus(state.phase, running, t);
  const [downloading, setDownloading] = useState(false);
  const [copying, setCopying] = useState(false);

  if (!enabled) return <ArtifactEmpty title={t("Artifact canvas")} message={t("This Agent is configured for conversational output.")} />;
  if (!displayedArtifact && state.phase === "loading") {
    return <div className="artifact-loading" role="status"><span className="micro-loader" />{t("Loading artifact…")}</div>;
  }
  if (!displayedArtifact && state.phase === "error") {
    return <ArtifactEmpty title={t("Artifact unavailable")} message={state.message ?? t("Unable to load artifact")} action={<button className="button secondary" onClick={workspace.reload}><RefreshCw size={13} />{t("Try again")}</button>} />;
  }
  if (!displayedArtifact) {
    return <ArtifactEmpty title={t("Artifact canvas")} message={t("Structured output will appear here as the agent works.")} />;
  }

  const copy = async () => {
    if (copying) return;
    setCopying(true);
    try {
      const content = displayedArtifact.content_type === "text/markdown"
        ? await portableRunMarkdown(displayedArtifact.content, citationsForArtifact(displayedArtifact, citationEvents), displayedArtifact.source_run_id, locale, { resolveHistory: displayedArtifact.source_run_id !== activeRunId })
        : displayedArtifact.content;
      await navigator.clipboard.writeText(content);
      onNotify(t("Artifact copied"));
    } catch { onNotify(t("Unable to copy artifact")); }
    finally { setCopying(false); }
  };
  const html = displayedArtifact.content_type === "text/html";
  const download = async (format: "docx" | "html" | "md") => {
    if (downloading || running || workspace.editorOpen) return;
    setDownloading(true);
    try {
      const file = await alcuinApi.downloadArtifact(displayedArtifact.id, format);
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = artifactDownloadName(displayedArtifact.title, format);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      onNotify(t("Artifact downloaded"));
    } catch (error) { onNotify(error instanceof Error ? error.message : t("Unable to download artifact")); }
    finally { setDownloading(false); }
  };

  return (
    <section className="artifact-workspace" data-phase={state.phase}>
      <div className="artifact-workspace-toolbar">
        <div className="artifact-resource-switcher" role="tablist" aria-label={t("Artifacts in this thread")} onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || workspace.editorOpen) return;
          const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button[role='tab']")];
          const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
          if (!buttons.length || current < 0) return;
          event.preventDefault();
          const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
          buttons[index]?.focus(); buttons[index]?.click();
        }}>
          {state.resources.map((resource) => (
            <button
              type="button"
              role="tab"
              aria-controls="artifact-resource-surface"
              aria-selected={resource.id === displayedArtifact.id}
              tabIndex={resource.id === displayedArtifact.id ? 0 : -1}
              className={clsx(resource.id === displayedArtifact.id && "active")}
              disabled={workspace.editorOpen}
              onClick={() => workspace.select(resource.id)}
              key={resource.id}
              title={resource.title}
            >{resource.content_type === "text/html" ? <Code2 size={12} /> : <FileText size={12} />}<span>{resource.title}</span></button>
          ))}
          {state.resources.length === 0 && running && <span className="artifact-live-label"><span />{t("Live draft")}</span>}
        </div>
        <div className="artifact-workspace-actions">
          <span className="artifact-state-chip" data-state={status.state} aria-live="polite" key={`${status.state}:${status.label}`}>
            {status.state === "busy" ? <LoaderCircle size={11} /> : status.state === "success" ? <Check size={11} /> : status.state === "warning" ? <AlertTriangle size={11} /> : null}
            {status.label}
          </span>
          {!workspace.editorOpen && <button type="button" className="icon-button quiet" aria-label={t("Copy artifact")} title={t("Copy artifact")} disabled={copying} aria-busy={copying} onClick={() => void copy()}>{copying ? <LoaderCircle className="attachment-loader" size={14} /> : <Copy size={14} />}</button>}
          {workspace.canEdit && !workspace.editorOpen && <button type="button" className="button secondary artifact-edit-button" onClick={workspace.beginEdit}><Edit3 size={13} />{t("Edit")}</button>}
          {!workspace.editorOpen && <button type="button" className="button secondary artifact-download-button" disabled={downloading || running} onClick={() => void download(html ? "html" : "docx")} aria-label={t(html ? "Download HTML" : "Download Word")} title={t(html ? "Download HTML" : "Download Word")}>
            {downloading ? <LoaderCircle size={13} className="attachment-loader" /> : <Download size={13} />}<span>{html ? "HTML" : "Word"}</span>
          </button>}
          {!workspace.editorOpen && !html && displayedArtifact.content_type !== "application/json" && <button type="button" className="icon-button quiet artifact-markdown-download" aria-label={t("Download Markdown")} title={t("Download Markdown")} disabled={downloading || running} onClick={() => void download("md")}><Code2 size={14} /></button>}
        </div>
      </div>

      <div id="artifact-resource-surface" className="artifact-workspace-surface" key={`${displayedArtifact.id}:${workspace.editorOpen ? "edit" : "preview"}`}>
        {workspace.editorOpen ? (
          <ArtifactEditor workspace={workspace} />
        ) : (
          html ? <ArtifactHtmlPreview key={displayedArtifact.id} title={displayedArtifact.title} content={displayedArtifact.content} running={running} /> : <ArtifactDocument artifact={displayedArtifact} updating={running} sourceRunRunning={displayedArtifact.source_run_id === activeRunId} citationEvents={citationEvents} />
        )}
      </div>
    </section>
  );
}

function ArtifactEditor({ workspace }: { workspace: ReturnType<typeof useArtifactWorkspace> }) {
  const { t } = useI18n();
  const { state } = workspace;
  const saving = state.phase === "saving";
  return (
    <form className="artifact-editor" onSubmit={(event) => { event.preventDefault(); void workspace.save(); }} onKeyDown={(event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (workspace.canSave) void workspace.save(); }
    }}>
      <div className="artifact-editor-heading">
        <div><span>{t("Editing artifact")}</span><small>{t("Changes are saved to this artifact only.")}</small></div>
        <button type="button" className="icon-button quiet" aria-label={t("Cancel editing")} onClick={workspace.cancel} disabled={saving}><X size={14} /></button>
      </div>
      <label className="artifact-title-field"><span>{t("Title")}</span><input autoFocus value={state.draftTitle} maxLength={200} disabled={saving} onChange={(event) => workspace.changeTitle(event.target.value)} /></label>
      <label className="artifact-content-field"><span>{t(workspace.selected?.content_type === "text/html" ? "HTML source" : "Content")}</span><textarea spellCheck={workspace.selected?.content_type !== "text/html"} value={state.draftContent} maxLength={500_000} disabled={saving} onChange={(event) => workspace.changeContent(event.target.value)} /></label>
      {state.phase === "conflict" && <div className="artifact-editor-notice conflict" role="alert"><AlertTriangle size={15} /><div><strong>{t("This artifact changed while you were editing")}</strong><p>{t("Refresh to load the latest saved content before editing again.")}</p></div><button type="button" className="button secondary" disabled={state.refreshingConflict} onClick={() => void workspace.refreshConflict()}>{state.refreshingConflict ? <LoaderCircle className="attachment-loader" size={13} /> : <RefreshCw size={13} />}{t("Refresh latest")}</button></div>}
      {state.phase === "error" && state.message && <div className="artifact-editor-notice error" role="alert"><AlertTriangle size={15} /><div><strong>{t("Save failed")}</strong><p>{state.message}</p></div></div>}
      <div className="artifact-editor-actions">
        <button type="button" className="button secondary" onClick={workspace.cancel} disabled={saving}>{t("Cancel")}</button>
        <button type="submit" className="button dark" disabled={!workspace.canSave}>{saving ? <LoaderCircle className="attachment-loader" size={13} /> : <Save size={13} />}{t(saving ? "Saving…" : state.phase === "error" ? "Try save again" : "Save")}</button>
      </div>
    </form>
  );
}

function ArtifactDocument({ artifact, updating, sourceRunRunning, citationEvents }: { artifact: ArtifactResource; updating: boolean; sourceRunRunning: boolean; citationEvents: readonly ExecutionEvent[] }) {
  const { t } = useI18n();
  const sources = useMemo(() => citationsForArtifact(artifact, citationEvents), [artifact, citationEvents]);
  const markdownBody = useMemo(() => artifactDocumentBody(artifact.content, artifact.title), [artifact.content, artifact.title]);
  return (
    <article className={clsx("artifact-document", updating && "updating")}>
      <div className="document-kicker">{t(updating ? "Working document" : "Artifact")}{updating && <span className="artifact-live-dot" />}</div>
      <h2>{artifact.title}</h2><div className="document-rule" />
      {artifact.content_type === "application/json" ? <pre className="artifact-plain-content">{artifact.content}</pre>
        : artifact.content_type === "text/plain" ? <div className="artifact-plain-content">{artifact.content}</div>
          : <CitationResponse content={markdownBody} variant="artifact" events={sources} runId={artifact.source_run_id} running={sourceRunRunning} />}
      <div className="artifact-signoff"><AlcuinMark size={30} /><span>{t("Prepared by Alcuin")}</span></div>
    </article>
  );
}

function ArtifactEmpty({ title, message, action }: { title: string; message: string; action?: React.ReactNode }) {
  return <div className="artifact-empty"><Sparkles size={22} /><h3>{title}</h3><p>{message}</p>{action}</div>;
}

function artifactStatus(
  phase: ReturnType<typeof useArtifactWorkspace>["state"]["phase"],
  running: boolean,
  t: ReturnType<typeof useI18n>["t"],
): { state: "neutral" | "busy" | "success" | "warning"; label: string } {
  if (phase === "saving" || phase === "loading") return { state: "busy", label: t(phase === "saving" ? "Saving…" : "Loading…") };
  if (phase === "saved") return { state: "success", label: t("Saved") };
  if (phase === "conflict" || phase === "error") return { state: "warning", label: t(phase === "conflict" ? "Refresh required" : "Action needed") };
  if (phase === "editing") return { state: "neutral", label: t("Editing") };
  if (running) return { state: "busy", label: t("Generating…") };
  return { state: "neutral", label: t("Ready") };
}
