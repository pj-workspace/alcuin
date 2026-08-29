"use client";

import type { Agent, Artifact, ContextAssembly, ExecutionEvent, Extension, ImageAttachment, Thread } from "@alcuin/contracts";
import NextImage from "next/image";
import {
  ArrowUp,
  ChevronDown,
  Clock3,
  Copy,
  Link2,
  Paperclip,
  Play,
  Plus,
  RotateCcw,
  Sparkles,
  TerminalSquare,
  X,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/shared/lib/api";
import { AlcuinMark } from "@/shared/components/alcuin-mark";
import { ConversationTimeline } from "@/features/studio/conversation-timeline";
import { ExtensionUIBlocks } from "@/features/studio/extension-ui-blocks";
import { MarkdownContent } from "@/shared/components/markdown-content";
import { Toast } from "@/shared/components/ui";
import { usePinnedTurnScroll } from "@/features/studio/use-pinned-turn-scroll";
import { useThreadSession } from "@/features/studio/use-thread-session";
import { resolveExtensionUIBlocks, type ResolvedExtensionUIBlock } from "@/features/extensions";
import { useI18n } from "@/shared/lib/i18n";

type CanvasTab = "artifact" | "trace" | "extensions" | "context";
type MobileSurface = "conversation" | "canvas";

export function StudioView({
  workspace,
  agent,
  extensions,
  activeThreadId,
  onThreadCreated,
  onRunCreated,
}: {
  workspace: { id: string; name: string };
  agent?: Agent;
  extensions: Extension[];
  activeThreadId: string | null;
  onThreadCreated: (thread: Thread) => void;
  onRunCreated: (runId: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const [canvasTab, setCanvasTab] = useState<CanvasTab>("artifact");
  const [mobileSurface, setMobileSurface] = useState<MobileSurface>("conversation");
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<ImageAttachment[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [composerFocused, setComposerFocused] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const hostContext = useMemo(() => ({
    page: "/workspace/records",
    record: { id: "REC-104", type: "project" },
  }), []);
  const session = useThreadSession({
    agent,
    threadId: activeThreadId,
    hostContext,
    onThreadCreated,
    onRunCreated,
  });
  const { state } = session;
  const running = session.running;
  const blocked = session.blocked;
  const latestTurn = state.turns.at(-1);
  const sendButtonLabel = state.phase === "waiting_for_approval"
    ? t("Resolve approval before sending another message")
    : t(blocked ? "Agent is running" : "Send message");
  const activeEvents = latestTurn?.events ?? [];
  const turnKey = `${state.thread?.id ?? "new"}:${latestTurn?.id ?? "empty"}`;
  const { anchorRef, endRef, scrollRef: timelineRef, spacerPx } = usePinnedTurnScroll(
    turnKey,
    activeEvents.length + (running ? 1 : 0),
  );

  const artifact = useMemo(() => {
    for (const turn of [...state.turns].reverse()) {
      const event = [...turn.events].reverse().find((item) => item.type === "artifact.updated");
      if (event) return event.payload.artifact as Artifact;
    }
    return undefined;
  }, [state.turns]);
  const citations = useMemo(() => {
    const seen = new Set<string>();
    return state.turns.flatMap((turn) => turn.events)
      .filter((event) => event.type === "citation.created")
      .filter((event) => {
        const key = String(event.payload.locator ?? event.payload.label ?? event.id);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  }, [state.turns]);
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
    if ((!value && selectedAttachments.length === 0) || !agent || blocked) return;
    try {
      await session.send(value, selectedAttachments, {
        onAccepted: () => {
          setPrompt("");
          setAttachments([]);
        },
      });
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Run failed"));
    }
  }

  async function submitExtensionAction(
    resolved: ResolvedExtensionUIBlock,
    tool: string,
    arguments_: Record<string, unknown>,
  ) {
    if (!agent || blocked) return;
    setMobileSurface("conversation");
    try {
      await session.runTool({
        label: `${resolved.block.title} · declarative extension action`,
        tool,
        arguments: arguments_,
        extensionManifestId: resolved.manifestId,
        uiBlockId: resolved.block.id,
      });
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Extension action failed"));
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

  async function decide(runId: string, approvalId: string, decision: "approved" | "denied") {
    setApprovalBusy(true);
    try {
      await alcuinApi.decideApproval(runId, approvalId, decision);
      await onRunCreated(runId);
      await session.refresh();
      showToast(t(decision === "approved" ? "Operation approved and completed" : "Operation denied — no changes made"));
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Approval failed"));
    } finally {
      setApprovalBusy(false);
    }
  }

  if (!agent) return null;

  return (
    <div className="studio-layout">
      <div className="mobile-studio-switch" role="tablist" aria-label={t("Studio panel")}>
        <button id="mobile-conversation-tab" role="tab" aria-controls="studio-conversation-panel" aria-selected={mobileSurface === "conversation"} className={clsx(mobileSurface === "conversation" && "active")} onClick={() => setMobileSurface("conversation")}>{t("Chat")}</button>
        <button id="mobile-canvas-tab" role="tab" aria-controls="studio-canvas-panel" aria-selected={mobileSurface === "canvas"} className={clsx(mobileSurface === "canvas" && "active")} onClick={() => setMobileSurface("canvas")}>{t("Canvas")}<span>{activeEvents.length + extensionBlocks.length}</span></button>
      </div>
      <section id="studio-conversation-panel" role="tabpanel" aria-labelledby="mobile-conversation-tab" className={clsx("conversation-pane", mobileSurface !== "conversation" && "mobile-surface-hidden")}>
        <header className="surface-header conversation-header">
          <div><div className="eyebrow"><span className="live-dot" />{t(agent.status === "published" ? "Published agent" : "Draft agent")} · v{agent.version}</div><h1>{agent.name}</h1></div>
          <div className="header-actions"><button className="button secondary"><Link2 size={14} />{t("Share")}</button><button className="button dark"><Play size={13} fill="currentColor" />{t("Deploy")}<ChevronDown size={13} /></button></div>
        </header>

        <div className="conversation-scroll" ref={timelineRef}>
          <ConversationTimeline
            turns={state.turns}
            workspaceName={workspace.name}
            threadTitle={state.thread?.title ?? t("New thread")}
            activeRunId={state.activeRunId}
            running={running}
            busy={approvalBusy}
            anchorRef={anchorRef}
            endRef={endRef}
            spacerPx={spacerPx}
            onCopy={(text) => { void navigator.clipboard.writeText(text); showToast(t("Response copied")); }}
            onDecision={(runId, approvalId, decision) => void decide(runId, approvalId, decision)}
          />
        </div>

        <div className="composer-wrap">
          <div className={clsx("composer", composerFocused && "focused", running && "running")}>
            {attachments.length > 0 && <div className="composer-attachments">{attachments.map((attachment, index) => <div className="composer-image" key={`${attachment.name}-${index}`}><NextImage src={attachment.data_url} alt={attachment.name} width={54} height={42} unoptimized /><button aria-label={t("Remove {name}", { name: attachment.name })} onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={11} /></button><span>{attachment.name}</span></div>)}</div>}
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onFocus={() => setComposerFocused(true)} onBlur={() => setComposerFocused(false)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder={t("Message {name}…", { name: agent.name })} />
            <div className="composer-footer">
              <div><button className="composer-tool" aria-label={t("More actions")}><Plus size={15} /></button><button className="composer-tool" aria-label={t("Attach images")} onClick={() => attachmentInputRef.current?.click()}><Paperclip size={15} /></button><input ref={attachmentInputRef} className="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple onChange={(event) => void addImages(event.target.files)} /><button className="context-chip"><span className="context-dot" />REC-104<ChevronDown size={11} /></button></div>
              <div className="composer-send-group"><span>↵</span><button className="send-button" aria-label={sendButtonLabel} disabled={(!prompt.trim() && attachments.length === 0) || blocked} onClick={() => void submit()}>{running ? <span className="send-spinner" /> : <ArrowUp size={17} />}</button></div>
            </div>
          </div>
          {state.turns.length === 0 && <div className="starter-row">{agent.definition.starter_prompts.slice(0, 2).map((starter) => <button key={starter} onClick={() => void submit(starter)}>{starter}</button>)}</div>}
        </div>
      </section>

      <aside id="studio-canvas-panel" role="tabpanel" aria-labelledby="mobile-canvas-tab" className={clsx("context-canvas", mobileSurface !== "canvas" && "mobile-surface-hidden")}>
        <header className="canvas-header">
          <div className="canvas-tabs" role="tablist" aria-label={t("Canvas")}>
            <button role="tab" aria-selected={canvasTab === "artifact"} className={clsx(canvasTab === "artifact" && "active")} onClick={() => setCanvasTab("artifact")}>{t("Artifact")}</button>
            <button role="tab" aria-selected={canvasTab === "trace"} className={clsx(canvasTab === "trace" && "active")} onClick={() => setCanvasTab("trace")}>{t("Trace")} <span>{activeEvents.length}</span></button>
            <button role="tab" aria-selected={canvasTab === "extensions"} className={clsx(canvasTab === "extensions" && "active")} onClick={() => setCanvasTab("extensions")}>{t("Blocks")} <span>{extensionBlocks.length}</span></button>
            <button role="tab" aria-selected={canvasTab === "context"} className={clsx(canvasTab === "context" && "active")} onClick={() => setCanvasTab("context")}>{t("Context")}</button>
          </div>
          <div><button className="icon-button quiet" title={t("Regenerate")}><RotateCcw size={14} /></button><button className="icon-button quiet" title={t("Copy")} onClick={() => { if (artifact) void navigator.clipboard.writeText(artifact.content); showToast(t("Artifact copied")); }}><Copy size={14} /></button></div>
        </header>
        <div className="canvas-content" key={canvasTab}>
          {canvasTab === "artifact" && (artifact ? <ArtifactDocument artifact={artifact} citationCount={citations.length} updating={running} /> : <div className="artifact-empty"><Sparkles size={22} /><h3>{t("Artifact canvas")}</h3><p>{t("Structured output will appear here as the agent works.")}</p></div>)}
          {canvasTab === "trace" && <TraceTimeline events={activeEvents} />}
          {canvasTab === "extensions" && <ExtensionUIBlocks blocks={extensionBlocks} events={activeEvents} context={hostContext} busy={blocked} onSubmit={submitExtensionAction} />}
          {canvasTab === "context" && <ContextInspector agent={agent} assembly={session.contextAssembly} />}
        </div>
        <footer className="canvas-footer"><span><Clock3 size={12} />{t("Updated just now")}</span><span>{canvasTab === "extensions" ? t("Declarative UI · {count} blocks", { count: extensionBlocks.length }) : `Markdown · v${artifact?.version ?? 1}`}</span></footer>
      </aside>
      {toast && <Toast message={toast} />}
    </div>
  );
}

function fileToImageAttachment(file: File): Promise<ImageAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ type: "image", name: file.name, media_type: file.type as ImageAttachment["media_type"], data_url: String(reader.result) });
    reader.onerror = () => reject(new Error(`Unable to read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function ArtifactDocument({ artifact, citationCount, updating }: { artifact: Artifact; citationCount: number; updating: boolean }) {
  const { t } = useI18n();
  return (
    <article className={clsx("artifact-document", updating && "updating")}>
      <div className="document-kicker">{t("Working document")}{updating && <span className="artifact-live-dot" />}</div>
      <h2>{artifact.title}</h2><div className="document-rule" /><MarkdownContent content={artifact.content} variant="artifact" />
      <div className="artifact-signoff"><AlcuinMark size={30} /><span>{t("Prepared by Alcuin")}<small>{citationCount > 0 ? t(citationCount === 1 ? "Grounded in {count} source" : "Grounded in {count} sources", { count: citationCount }) : t("No external sources used")}</small></span></div>
    </article>
  );
}

function TraceTimeline({ events }: { events: ExecutionEvent[] }) {
  const { t } = useI18n();
  return <div className="trace-timeline">{summarizeTraceEvents(events).map((event) => (
    <div className="trace-item" key={event.id}><span className={clsx("trace-node", event.type === "run.completed" && "done")} /><div><strong>{event.type}</strong><p>{event.payload.summary ?? event.payload.result_summary ?? event.payload.label ?? event.payload.status ?? t("Event recorded")}</p><small>#{event.sequence} · {new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</small></div></div>
  ))}</div>;
}

function summarizeTraceEvents(events: ExecutionEvent[]): ExecutionEvent[] {
  const visible: ExecutionEvent[] = [];
  for (const event of events) {
    if ((event.type === "message.delta" || event.type === "reasoning.delta") && visible.at(-1)?.type === event.type) continue;
    visible.push(event);
  }
  return visible;
}

function ContextInspector({ agent, assembly }: { agent: Agent; assembly: ContextAssembly | null }) {
  const { t } = useI18n();
  return <div className="context-inspector">
    <div className="context-group"><label>{t("Agent version")}</label><strong>{agent.name} · v{agent.version}</strong><span>{t("Immutable published definition")}</span></div>
    {assembly ? <>
      <div className="context-metrics">
        <div><span>{t("Estimated input")}</span><strong>{assembly.estimated_input_tokens.toLocaleString()}</strong><small>tokens</small></div>
        <div><span>{t("Effective budget")}</span><strong>{assembly.effective_budget_tokens.toLocaleString()}</strong><small>tokens</small></div>
      </div>
      <div className="context-group">
        <label>{t("Assembled context")}</label>
        <div className="context-entry-list">{assembly.entries.map((entry, index) => <div className={clsx("context-entry", !entry.included && "excluded")} key={`${entry.kind}-${entry.label}-${index}`}><span>{entry.kind}</span><strong>{entry.label}</strong><small>{entry.token_estimate == null ? t("Token estimate unavailable") : t("{count} tokens", { count: entry.token_estimate })}</small></div>)}</div>
      </div>
      <div className="context-group context-provenance"><label>{t("Context provenance")}</label><span>{assembly.agent_version_id}</span><span>{t("Messages through #{sequence}", { sequence: assembly.message_sequence_through })}</span><span>{assembly.active_compaction_id ? t("Compaction {id}", { id: assembly.active_compaction_id }) : t("No active compaction")}</span></div>
    </> : <div className="context-empty"><Sparkles size={18} /><strong>{t("Context snapshot unavailable")}</strong><span>{t("Run this thread to inspect the operator-safe assembled context.")}</span></div>}
    <div className="context-group"><label>{t("Bound capabilities")}</label>{agent.definition.tools.map((tool) => <span className="tool-binding" key={tool}><TerminalSquare size={12} />{tool}</span>)}</div>
  </div>;
}
