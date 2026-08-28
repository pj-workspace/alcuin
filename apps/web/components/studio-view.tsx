"use client";

import type { Agent, Artifact, ExecutionEvent, Extension, ImageAttachment } from "@alcuin/contracts";
import NextImage from "next/image";
import {
  ArrowUp,
  Check,
  ChevronDown,
  CircleStop,
  Clock3,
  Copy,
  FileText,
  Link2,
  Paperclip,
  Play,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/lib/api";
import { AlcuinMark } from "@/components/alcuin-mark";
import { ExtensionUIBlocks } from "@/components/extension-ui-blocks";
import { MarkdownContent } from "@/components/markdown-content";
import { RunOutput } from "@/components/run-output";
import { Toast } from "@/components/ui";
import { usePinnedTurnScroll } from "@/components/use-pinned-turn-scroll";
import { resolveExtensionUIBlocks, type ResolvedExtensionUIBlock } from "@/lib/extension-ui";
import { useI18n } from "@/lib/i18n";

type CanvasTab = "artifact" | "trace" | "extensions" | "context";
type MobileSurface = "conversation" | "canvas";

export function StudioView({
  workspace,
  agent,
  extensions,
  initialEvents,
  onRunCreated,
}: {
  workspace: { id: string; name: string };
  agent?: Agent;
  extensions: Extension[];
  initialEvents: ExecutionEvent[];
  onRunCreated: (runId: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const [events, setEvents] = useState(initialEvents);
  const [canvasTab, setCanvasTab] = useState<CanvasTab>("artifact");
  const [mobileSurface, setMobileSurface] = useState<MobileSurface>("conversation");
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<ImageAttachment[]>([]);
  const [lastAttachments, setLastAttachments] = useState<ImageAttachment[]>([]);
  const [lastPrompt, setLastPrompt] = useState("Summarize the active checkout incident and prepare a handoff brief.");
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(initialEvents[0]?.run_id ?? null);
  const [toast, setToast] = useState<string | null>(null);
  const [composerFocused, setComposerFocused] = useState(false);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const hostContext = useMemo(() => ({
    page: "/operations/incidents",
    record: { id: "INC-104", type: "incident" },
  }), []);

  useEffect(() => {
    if (!running) {
      setEvents(initialEvents);
      setRunId(initialEvents[0]?.run_id ?? null);
    }
  }, [initialEvents, running]);
  const turnKey = `${runId ?? "initial"}:${lastPrompt}:${lastAttachments.length}`;
  const { anchorRef, endRef, scrollRef: timelineRef, spacerPx } = usePinnedTurnScroll(
    turnKey,
    events.length + (running ? 1 : 0),
  );

  const artifact = useMemo(() => {
    const event = [...events].reverse().find((item) => item.type === "artifact.updated");
    return event?.payload.artifact as Artifact | undefined;
  }, [events]);
  const visibleArtifact = running ? undefined : artifact;
  const assistantText = events.filter((event) => event.type === "message.delta").map((event) => event.payload.delta).join("");
  const approval = [...events].reverse().find((event) => event.type === "approval.required");
  const citations = useMemo(() => {
    const seen = new Set<string>();
    return events
      .filter((event) => event.type === "citation.created")
      .filter((event) => {
        const key = String(event.payload.locator ?? event.payload.label ?? event.id);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  }, [events]);
  const extensionBlocks = useMemo(
    () => agent ? resolveExtensionUIBlocks(agent, extensions) : [],
    [agent, extensions],
  );

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 2200);
  };

  async function submit(input = prompt, selectedAttachments = attachments) {
    const value = input.trim();
    if ((!value && selectedAttachments.length === 0) || !agent || running) return;
    setPrompt("");
    setAttachments([]);
    setLastPrompt(value || t("Describe the attached image."));
    setLastAttachments(selectedAttachments);
    setEvents([]);
    setRunning(true);
    try {
      const thread = await alcuinApi.createThread(agent.id, hostContext);
      const run = await alcuinApi.createRun(thread.id, value, selectedAttachments);
      setRunId(run.id);
      await alcuinApi.streamRun(run.id, (event) => setEvents((current) => [...current, event]));
      await onRunCreated(run.id);
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Run failed"));
    } finally {
      setRunning(false);
    }
  }

  async function submitExtensionAction(
    resolved: ResolvedExtensionUIBlock,
    tool: string,
    arguments_: Record<string, unknown>,
  ) {
    if (!agent || running) return;
    setMobileSurface("conversation");
    setLastPrompt(`${resolved.extensionName} · ${resolved.block.title}`);
    setLastAttachments([]);
    setEvents([]);
    setRunning(true);
    try {
      const thread = await alcuinApi.createThread(agent.id, hostContext);
      const run = await alcuinApi.createToolRun(
        thread.id,
        `${resolved.block.title} · declarative extension action`,
        tool,
        arguments_,
        resolved.manifestId,
        resolved.block.id,
      );
      setRunId(run.id);
      await alcuinApi.streamRun(run.id, (event) => setEvents((current) => [...current, event]));
      await onRunCreated(run.id);
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Extension action failed"));
    } finally {
      setRunning(false);
    }
  }

  async function addImages(files: FileList | null) {
    if (!files) return;
    const accepted = Array.from(files).filter((file) =>
      ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type),
    );
    const valid = accepted.filter((file) => file.size <= 5 * 1024 * 1024);
    if (valid.length !== files.length) showToast(t("Use PNG, JPEG, WebP, or GIF images up to 5 MiB"));
    const available = valid.slice(0, Math.max(0, 4 - attachments.length));
    const next = await Promise.all(available.map(fileToImageAttachment));
    setAttachments((current) => [...current, ...next].slice(0, 4));
    if (attachmentInputRef.current) attachmentInputRef.current.value = "";
  }

  async function decide(decision: "approved" | "denied") {
    const approvalId = approval?.payload.approval_id as string | undefined;
    if (!runId || !approvalId) return;
    setRunning(true);
    try {
      await alcuinApi.decideApproval(runId, approvalId, decision);
      const run = await alcuinApi.getRun(runId);
      setEvents(run.events);
      showToast(t(decision === "approved" ? "Operation approved and completed" : "Operation denied — no changes made"));
      await onRunCreated(runId);
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Approval failed"));
    } finally {
      setRunning(false);
    }
  }

  if (!agent) return null;

  return (
    <div className="studio-layout">
      <div className="mobile-studio-switch" role="tablist" aria-label={t("Studio panel")}>
        <button
          id="mobile-conversation-tab"
          role="tab"
          aria-controls="studio-conversation-panel"
          aria-selected={mobileSurface === "conversation"}
          className={clsx(mobileSurface === "conversation" && "active")}
          onClick={() => setMobileSurface("conversation")}
        >
          {t("Chat")}
        </button>
        <button
          id="mobile-canvas-tab"
          role="tab"
          aria-controls="studio-canvas-panel"
          aria-selected={mobileSurface === "canvas"}
          className={clsx(mobileSurface === "canvas" && "active")}
          onClick={() => setMobileSurface("canvas")}
        >
          {t("Canvas")}
          <span>{events.length + extensionBlocks.length}</span>
        </button>
      </div>
      <section
        id="studio-conversation-panel"
        role="tabpanel"
        aria-labelledby="mobile-conversation-tab"
        className={clsx("conversation-pane", mobileSurface !== "conversation" && "mobile-surface-hidden")}
      >
        <header className="surface-header conversation-header">
          <div>
            <div className="eyebrow"><span className="live-dot" />{t(agent.status === "published" ? "Published agent" : "Draft agent")} · v{agent.version}</div>
            <h1>{agent.name}</h1>
          </div>
          <div className="header-actions">
            <button className="button secondary"><Link2 size={14} />{t("Share")}</button>
            <button className="button dark"><Play size={13} fill="currentColor" />{t("Deploy")}<ChevronDown size={13} /></button>
          </div>
        </header>

        <div className="conversation-scroll" ref={timelineRef}>
          <div className="conversation-inner">
            <div className="thread-meta"><span>{workspace.name}</span><i />{t("Operations review")}<i />{t("Now")}</div>
            <article className="user-turn" ref={anchorRef}>
              <div className="user-message">{lastAttachments.length > 0 && <div className="turn-images">{lastAttachments.map((attachment) => <NextImage key={`${attachment.name}-${attachment.data_url.length}`} src={attachment.data_url} alt={attachment.name} width={92} height={68} unoptimized />)}</div>}<p>{lastPrompt}</p></div>
            </article>

            <article className="agent-turn" ref={endRef}>
              <div className="agent-turn-content">
                <RunOutput
                  events={events}
                  running={running}
                  assistantText={assistantText}
                  onCopy={() => { void navigator.clipboard.writeText(assistantText); showToast(t("Response copied")); }}
                />
                {approval && !events.some((event) => event.type === "run.completed") && (
                  <div className="approval-card">
                    <div className="approval-top"><span className="approval-icon"><ShieldCheck size={16} /></span><div><strong>{approval.payload.title}</strong><p>{approval.payload.description}</p></div><span className="risk-label">{t("High impact")}</span></div>
                    <div className="approval-command"><code>{approval.payload.tool}</code><span>{JSON.stringify(approval.payload.arguments)}</span></div>
                    <div className="approval-actions"><button className="button secondary" onClick={() => void decide("denied")}><X size={14} />{t("Deny")}</button><button className="button dark" onClick={() => void decide("approved")}><Check size={14} />{t("Approve once")}</button></div>
                  </div>
                )}
                {citations.length > 0 && (
                  <button className="citation-chip" title={citations.map((event) => String(event.payload.label ?? event.payload.source ?? t("Source"))).join(" · ")}><FileText size={12} />{t("Sources")} <span>{citations.length}</span></button>
                )}
              </div>
            </article>
            <div className="turn-stream-spacer" style={{ height: spacerPx }} aria-hidden />
          </div>
        </div>

        <div className="composer-wrap">
          <div className={clsx("composer", composerFocused && "focused")}>
            {attachments.length > 0 && <div className="composer-attachments">{attachments.map((attachment, index) => <div className="composer-image" key={`${attachment.name}-${index}`}><NextImage src={attachment.data_url} alt={attachment.name} width={54} height={42} unoptimized /><button aria-label={t("Remove {name}", { name: attachment.name })} onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={11} /></button><span>{attachment.name}</span></div>)}</div>}
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onFocus={() => setComposerFocused(true)}
              onBlur={() => setComposerFocused(false)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); }
              }}
              placeholder={t("Message {name}…", { name: agent.name })}
            />
            <div className="composer-footer">
              <div><button className="composer-tool"><Plus size={15} /></button><button className="composer-tool" aria-label={t("Attach images")} onClick={() => attachmentInputRef.current?.click()}><Paperclip size={15} /></button><input ref={attachmentInputRef} className="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple onChange={(event) => void addImages(event.target.files)} /><button className="context-chip"><span className="context-dot" />INC-104<ChevronDown size={11} /></button></div>
              <div className="composer-send-group"><span>⌘ ↵</span><button className="send-button" disabled={(!prompt.trim() && attachments.length === 0) || running} onClick={() => void submit()}>{running ? <CircleStop size={16} /> : <ArrowUp size={17} />}</button></div>
            </div>
          </div>
          <div className="starter-row">
            {agent.definition.starter_prompts.slice(0, 2).map((starter) => <button key={starter} onClick={() => void submit(starter)}>{starter}</button>)}
          </div>
        </div>
      </section>

      <aside
        id="studio-canvas-panel"
        role="tabpanel"
        aria-labelledby="mobile-canvas-tab"
        className={clsx("context-canvas", mobileSurface !== "canvas" && "mobile-surface-hidden")}
      >
        <header className="canvas-header">
          <div className="canvas-tabs">
            <button className={clsx(canvasTab === "artifact" && "active")} onClick={() => setCanvasTab("artifact")}>{t("Artifact")}</button>
            <button className={clsx(canvasTab === "trace" && "active")} onClick={() => setCanvasTab("trace")}>{t("Trace")} <span>{events.length}</span></button>
            <button className={clsx(canvasTab === "extensions" && "active")} onClick={() => setCanvasTab("extensions")}>{t("Blocks")} <span>{extensionBlocks.length}</span></button>
            <button className={clsx(canvasTab === "context" && "active")} onClick={() => setCanvasTab("context")}>{t("Context")}</button>
          </div>
          <div><button className="icon-button quiet" title={t("Regenerate")}><RotateCcw size={14} /></button><button className="icon-button quiet" title={t("Copy")} onClick={() => { if (artifact) void navigator.clipboard.writeText(artifact.content); showToast(t("Artifact copied")); }}><Copy size={14} /></button></div>
        </header>
        <div className="canvas-content">
          {canvasTab === "artifact" && (
            visibleArtifact ? <ArtifactDocument artifact={visibleArtifact} citationCount={citations.length} /> : <div className="artifact-empty"><Sparkles size={22} /><h3>{t("Artifact canvas")}</h3><p>{t(running ? "The artifact will settle here when the response is complete." : "Structured output will appear here as the agent works.")}</p></div>
          )}
          {canvasTab === "trace" && <TraceTimeline events={events} />}
          {canvasTab === "extensions" && <ExtensionUIBlocks blocks={extensionBlocks} events={events} context={hostContext} busy={running} onSubmit={submitExtensionAction} />}
          {canvasTab === "context" && <ContextInspector agent={agent} />}
        </div>
        <footer className="canvas-footer"><span><Clock3 size={12} />{t("Updated just now")}</span><span>{canvasTab === "extensions" ? t("Declarative UI · {count} blocks", { count: extensionBlocks.length }) : `Markdown · v${visibleArtifact?.version ?? 1}`}</span></footer>
      </aside>
      {toast && <Toast message={toast} />}
    </div>
  );
}

function fileToImageAttachment(file: File): Promise<ImageAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({
      type: "image",
      name: file.name,
      media_type: file.type as ImageAttachment["media_type"],
      data_url: String(reader.result),
    });
    reader.onerror = () => reject(new Error(`Unable to read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function ArtifactDocument({ artifact, citationCount }: { artifact: Artifact; citationCount: number }) {
  const { t } = useI18n();
  return (
    <article className="artifact-document">
      <div className="document-kicker">{t("Operations / Incident brief")}</div>
      <h2>{artifact.title}</h2>
      <div className="document-rule" />
      <MarkdownContent content={artifact.content} variant="artifact" />
      <div className="artifact-signoff"><AlcuinMark size={30} /><span>{t("Prepared by Alcuin")}<small>{citationCount > 0 ? t(citationCount === 1 ? "Grounded in {count} source" : "Grounded in {count} sources", { count: citationCount }) : t("No external sources used")}</small></span></div>
    </article>
  );
}

function TraceTimeline({ events }: { events: ExecutionEvent[] }) {
  const { t } = useI18n();
  return <div className="trace-timeline">{events.map((event) => (
    <div className="trace-item" key={event.id}>
      <span className={clsx("trace-node", event.type === "run.completed" && "done")} />
      <div><strong>{event.type}</strong><p>{event.payload.summary ?? event.payload.result_summary ?? event.payload.label ?? event.payload.status ?? t("Event recorded")}</p><small>#{event.sequence} · {new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</small></div>
    </div>
  ))}</div>;
}

function ContextInspector({ agent }: { agent: Agent }) {
  const { t } = useI18n();
  return <div className="context-inspector">
    <div className="context-group"><label>{t("Agent version")}</label><strong>{agent.name} · v{agent.version}</strong><span>{t("Immutable published definition")}</span></div>
    <div className="context-group"><label>{t("Host context")}</label><code>{`{\n  "page": "/operations/incidents",\n  "record": { "id": "INC-104" }\n}`}</code></div>
    <div className="context-group"><label>{t("Bound capabilities")}</label>{agent.definition.tools.map((tool) => <span className="tool-binding" key={tool}><TerminalSquare size={12} />{tool}</span>)}</div>
  </div>;
}
