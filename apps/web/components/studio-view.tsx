"use client";

import type { Agent, Artifact, ExecutionEvent, ImageAttachment } from "@alcuin/contracts";
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
import { MarkdownContent } from "@/components/markdown-content";
import { RunOutput } from "@/components/run-output";
import { Toast } from "@/components/ui";
import { usePinnedTurnScroll } from "@/components/use-pinned-turn-scroll";

type CanvasTab = "artifact" | "trace" | "context";

export function StudioView({
  workspace,
  agent,
  initialEvents,
  onRunCreated,
}: {
  workspace: { id: string; name: string };
  agent?: Agent;
  initialEvents: ExecutionEvent[];
  onRunCreated: () => Promise<void>;
}) {
  const [events, setEvents] = useState(initialEvents);
  const [canvasTab, setCanvasTab] = useState<CanvasTab>("artifact");
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<ImageAttachment[]>([]);
  const [lastAttachments, setLastAttachments] = useState<ImageAttachment[]>([]);
  const [lastPrompt, setLastPrompt] = useState("Summarize the active checkout incident and prepare a handoff brief.");
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(initialEvents[0]?.run_id ?? null);
  const [toast, setToast] = useState<string | null>(null);
  const [composerFocused, setComposerFocused] = useState(false);
  const attachmentInputRef = useRef<HTMLInputElement>(null);

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

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 2200);
  };

  async function submit(input = prompt, selectedAttachments = attachments) {
    const value = input.trim();
    if ((!value && selectedAttachments.length === 0) || !agent || running) return;
    setPrompt("");
    setAttachments([]);
    setLastPrompt(value || "Describe the attached image.");
    setLastAttachments(selectedAttachments);
    setEvents([]);
    setRunning(true);
    try {
      const thread = await alcuinApi.createThread(agent.id, {
        page: "/operations/incidents",
        record: { id: "INC-104", type: "incident" },
      });
      const run = await alcuinApi.createRun(thread.id, value, selectedAttachments);
      setRunId(run.id);
      await alcuinApi.streamRun(run.id, (event) => setEvents((current) => [...current, event]));
      await onRunCreated();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Run failed");
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
    if (valid.length !== files.length) showToast("Use PNG, JPEG, WebP, or GIF images up to 5 MiB");
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
      showToast(decision === "approved" ? "Operation approved and completed" : "Operation denied — no changes made");
      await onRunCreated();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Approval failed");
    } finally {
      setRunning(false);
    }
  }

  if (!agent) return null;

  return (
    <div className="studio-layout">
      <section className="conversation-pane">
        <header className="surface-header conversation-header">
          <div>
            <div className="eyebrow"><span className="live-dot" />Published agent · v{agent.version}</div>
            <h1>{agent.name}</h1>
          </div>
          <div className="header-actions">
            <button className="button secondary"><Link2 size={14} />Share</button>
            <button className="button dark"><Play size={13} fill="currentColor" />Deploy<ChevronDown size={13} /></button>
          </div>
        </header>

        <div className="conversation-scroll" ref={timelineRef}>
          <div className="conversation-inner">
            <div className="thread-meta"><span>{workspace.name}</span><i />Operations review<i />Now</div>
            <article className="user-turn" ref={anchorRef}>
              <div className="user-message">{lastAttachments.length > 0 && <div className="turn-images">{lastAttachments.map((attachment) => <NextImage key={`${attachment.name}-${attachment.data_url.length}`} src={attachment.data_url} alt={attachment.name} width={92} height={68} unoptimized />)}</div>}<p>{lastPrompt}</p></div>
            </article>

            <article className="agent-turn" ref={endRef}>
              <div className="agent-turn-content">
                <RunOutput
                  events={events}
                  running={running}
                  assistantText={assistantText}
                  onCopy={() => { void navigator.clipboard.writeText(assistantText); showToast("Response copied"); }}
                />
                {approval && !events.some((event) => event.type === "run.completed") && (
                  <div className="approval-card">
                    <div className="approval-top"><span className="approval-icon"><ShieldCheck size={16} /></span><div><strong>{approval.payload.title}</strong><p>{approval.payload.description}</p></div><span className="risk-label">High impact</span></div>
                    <div className="approval-command"><code>{approval.payload.tool}</code><span>{JSON.stringify(approval.payload.arguments)}</span></div>
                    <div className="approval-actions"><button className="button secondary" onClick={() => void decide("denied")}><X size={14} />Deny</button><button className="button dark" onClick={() => void decide("approved")}><Check size={14} />Approve once</button></div>
                  </div>
                )}
                {citations.length > 0 && (
                  <button className="citation-chip" title={citations.map((event) => String(event.payload.label ?? event.payload.source ?? "Source")).join(" · ")}><FileText size={12} />Sources <span>{citations.length}</span></button>
                )}
              </div>
            </article>
            <div className="turn-stream-spacer" style={{ height: spacerPx }} aria-hidden />
          </div>
        </div>

        <div className="composer-wrap">
          <div className={clsx("composer", composerFocused && "focused")}>
            {attachments.length > 0 && <div className="composer-attachments">{attachments.map((attachment, index) => <div className="composer-image" key={`${attachment.name}-${index}`}><NextImage src={attachment.data_url} alt={attachment.name} width={54} height={42} unoptimized /><button aria-label={`Remove ${attachment.name}`} onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={11} /></button><span>{attachment.name}</span></div>)}</div>}
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onFocus={() => setComposerFocused(true)}
              onBlur={() => setComposerFocused(false)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); }
              }}
              placeholder={`Message ${agent.name}…`}
            />
            <div className="composer-footer">
              <div><button className="composer-tool"><Plus size={15} /></button><button className="composer-tool" aria-label="Attach images" onClick={() => attachmentInputRef.current?.click()}><Paperclip size={15} /></button><input ref={attachmentInputRef} className="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple onChange={(event) => void addImages(event.target.files)} /><button className="context-chip"><span className="context-dot" />INC-104<ChevronDown size={11} /></button></div>
              <div className="composer-send-group"><span>⌘ ↵</span><button className="send-button" disabled={(!prompt.trim() && attachments.length === 0) || running} onClick={() => void submit()}>{running ? <CircleStop size={16} /> : <ArrowUp size={17} />}</button></div>
            </div>
          </div>
          <div className="starter-row">
            {agent.definition.starter_prompts.slice(0, 2).map((starter) => <button key={starter} onClick={() => void submit(starter)}>{starter}</button>)}
          </div>
        </div>
      </section>

      <aside className="context-canvas">
        <header className="canvas-header">
          <div className="canvas-tabs">
            <button className={clsx(canvasTab === "artifact" && "active")} onClick={() => setCanvasTab("artifact")}>Artifact</button>
            <button className={clsx(canvasTab === "trace" && "active")} onClick={() => setCanvasTab("trace")}>Trace <span>{events.length}</span></button>
            <button className={clsx(canvasTab === "context" && "active")} onClick={() => setCanvasTab("context")}>Context</button>
          </div>
          <div><button className="icon-button quiet" title="Regenerate"><RotateCcw size={14} /></button><button className="icon-button quiet" title="Copy" onClick={() => { if (artifact) void navigator.clipboard.writeText(artifact.content); showToast("Artifact copied"); }}><Copy size={14} /></button></div>
        </header>
        <div className="canvas-content">
          {canvasTab === "artifact" && (
            visibleArtifact ? <ArtifactDocument artifact={visibleArtifact} citationCount={citations.length} /> : <div className="artifact-empty"><Sparkles size={22} /><h3>Artifact canvas</h3><p>{running ? "The artifact will settle here when the response is complete." : "Structured output will appear here as the agent works."}</p></div>
          )}
          {canvasTab === "trace" && <TraceTimeline events={events} />}
          {canvasTab === "context" && <ContextInspector agent={agent} />}
        </div>
        <footer className="canvas-footer"><span><Clock3 size={12} />Updated just now</span><span>Markdown · v{visibleArtifact?.version ?? 1}</span></footer>
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
  return (
    <article className="artifact-document">
      <div className="document-kicker">Operations / Incident brief</div>
      <h2>{artifact.title}</h2>
      <div className="document-rule" />
      <MarkdownContent content={artifact.content} variant="artifact" />
      <div className="artifact-signoff"><AlcuinMark size={30} /><span>Prepared by Alcuin<small>{citationCount > 0 ? `Grounded in ${citationCount} source${citationCount === 1 ? "" : "s"}` : "No external sources used"}</small></span></div>
    </article>
  );
}

function TraceTimeline({ events }: { events: ExecutionEvent[] }) {
  return <div className="trace-timeline">{events.map((event) => (
    <div className="trace-item" key={event.id}>
      <span className={clsx("trace-node", event.type === "run.completed" && "done")} />
      <div><strong>{event.type}</strong><p>{event.payload.summary ?? event.payload.result_summary ?? event.payload.label ?? event.payload.status ?? "Event recorded"}</p><small>#{event.sequence} · {new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</small></div>
    </div>
  ))}</div>;
}

function ContextInspector({ agent }: { agent: Agent }) {
  return <div className="context-inspector">
    <div className="context-group"><label>Agent version</label><strong>{agent.name} · v{agent.version}</strong><span>Immutable published definition</span></div>
    <div className="context-group"><label>Host context</label><code>{`{\n  "page": "/operations/incidents",\n  "record": { "id": "INC-104" }\n}`}</code></div>
    <div className="context-group"><label>Bound capabilities</label>{agent.definition.tools.map((tool) => <span className="tool-binding" key={tool}><TerminalSquare size={12} />{tool}</span>)}</div>
  </div>;
}
