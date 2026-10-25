import type { ExecutionEvent, Run } from "@alcuin/contracts";

export const RUN_FILTERS = ["all", "active", "needs_attention", "completed", "failed", "cancelled"] as const;
export type RunFilter = (typeof RUN_FILTERS)[number];
const terminal = new Set(["completed", "failed", "cancelled"]);

export function filterRuns(runs: readonly Run[], query = "", filter: RunFilter = "all"): Run[] {
  const needle = query.trim().toLocaleLowerCase();
  return runs.filter((run) => {
    const matches = filter === "all" || (filter === "active" ? !terminal.has(run.status)
      : filter === "needs_attention" ? ["waiting_for_input", "waiting_for_approval"].includes(run.status)
        : run.status === filter);
    return matches && (!needle || [run.title, run.input, run.agent_name, run.id].some((value) => value?.toLocaleLowerCase().includes(needle)));
  });
}

/** Wall time, including human waits. Missing completion time is never inferred. */
export function runDurationMs(run: Run, now?: number): number | null {
  const start = Date.parse(run.created_at);
  const end = run.completed_at ? Date.parse(run.completed_at) : terminal.has(run.status) ? NaN : now;
  return Number.isFinite(start) && typeof end === "number" && Number.isFinite(end) && end >= start ? end - start : null;
}

export function receiptEvents(runId: string, events: readonly ExecutionEvent[]): ExecutionEvent[] {
  const ids = new Set<string>();
  return events.filter((event) => event.run_id === runId && Number.isFinite(event.sequence))
    .slice().sort((a, b) => a.sequence - b.sequence || a.id.localeCompare(b.id))
    .filter((event) => { if (ids.has(event.id)) return false; ids.add(event.id); return true; });
}

export type ReceiptStatus = "recorded" | "pending" | "approved" | "denied" | "succeeded" | "failed" | "cancelled" | "unknown";
export interface ReceiptItem {
  id: string;
  sequence: number;
  timestamp: string;
  kind: "lifecycle" | "tool" | "approval" | "input" | "artifact" | "citation";
  label: string;
  status: ReceiptStatus;
  name?: string;
}
export interface ToolReceipt {
  callId: string;
  name: string;
  status: ReceiptStatus;
  permission?: "pending" | "approved" | "denied";
}
export interface ReceiptCitation { id: string; title: string; url: string | null; documentId?: string; pageNumber?: number; chunkIndex?: number }
export interface RunReceipt { items: ReceiptItem[]; tools: ToolReceipt[]; citations: ReceiptCitation[] }
export interface ReceiptArtifact { id: string; title: string; kind: string }

function ownedEvents(run: Run, events: readonly ExecutionEvent[]): ExecutionEvent[] {
  // Legacy event contracts omit workspace_id; the scoped API and exact run ID
  // provide ownership. Any explicit workspace field must agree as well.
  return receiptEvents(run.id, events.filter((event) => !("workspace_id" in event) || event.workspace_id === run.workspace_id));
}

function ownedArtifact(run: Run, event: ExecutionEvent): Record<string, unknown> | null {
  const artifact: unknown = event.payload.artifact;
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) return null;
  const record = artifact as Record<string, unknown>;
  return record.workspace_id === run.workspace_id && record.thread_id === run.thread_id && record.source_run_id === run.id && id(record.id) ? record : null;
}

export function collectReceiptArtifacts(run: Run, events: readonly ExecutionEvent[]): ReceiptArtifact[] {
  const artifacts = new Map<string, { version: number; artifact: ReceiptArtifact }>();
  for (const event of ownedEvents(run, events)) {
    if (event.type !== "artifact.updated") continue;
    const artifact = ownedArtifact(run, event);
    if (!artifact) continue;
    const artifactId = id(artifact.id)!;
    const version = typeof artifact.version === "number" && Number.isFinite(artifact.version) ? artifact.version : 0;
    const previous = artifacts.get(artifactId);
    if (previous && previous.version > version) continue;
    artifacts.set(artifactId, { version, artifact: { id: artifactId, title: text(artifact.title) ?? "Deliverable", kind: text(artifact.kind) ?? "document" } });
  }
  return [...artifacts.values()].map(({ artifact }) => artifact);
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 240) : undefined;
}
// IDs must retain their exact bytes: display sanitization must not merge calls.
function id(value: unknown): string | undefined { return typeof value === "string" && value.length ? value : undefined; }
function nonnegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}
function safeUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
function outcome(value: unknown): ReceiptStatus {
  return ["succeeded", "failed", "denied", "cancelled"].includes(String(value)) ? value as ReceiptStatus : "unknown";
}

/** Only allowlisted receipt fields leave this projection; payloads stay private. */
export function buildRunReceipt(run: Run, events: readonly ExecutionEvent[]): RunReceipt {
  const items: ReceiptItem[] = [];
  const tools = new Map<string, ToolReceipt>();
  const approvals = new Map<string, string>();
  const citations: ReceiptCitation[] = [];
  const citationIds = new Set<string>();
  const lifecycle: Record<string, [string, ReceiptStatus]> = {
    "run.started": ["Work started", "recorded"], "run.completed": ["Work completed", "succeeded"],
    "run.failed": ["Work failed", "failed"], "run.cancel_requested": ["Stop requested", "pending"],
    "run.cancelled": ["Work stopped", "cancelled"],
  };
  for (const event of ownedEvents(run, events)) {
    const p: Record<string, unknown> = event.payload;
    const base = { id: event.id, sequence: event.sequence, timestamp: event.timestamp };
    const callId = id(p.call_id);
    const name = text(p.tool) ?? text(p.name) ?? "Tool";
    if (lifecycle[event.type]) {
      const [label, status] = lifecycle[event.type];
      items.push({ ...base, kind: "lifecycle", label, status });
    } else if (event.type === "tool.requested" || event.type === "tool.completed") {
      const status = event.type === "tool.completed" ? outcome(p.status) : "pending";
      if (callId) {
        const previous = tools.get(callId);
        tools.set(callId, { callId, name: previous?.name ?? name, status, ...(previous?.permission ? { permission: previous.permission } : {}) });
      }
      items.push({ ...base, kind: "tool", label: event.type === "tool.requested" ? "Tool requested" : "Tool outcome recorded", status, name });
    } else if (event.type === "approval.required") {
      const approvalId = id(p.approval_id);
      if (callId) {
        if (approvalId) approvals.set(approvalId, callId);
        const previous = tools.get(callId);
        tools.set(callId, { callId, name: previous?.name ?? name, status: previous?.status ?? "pending", permission: "pending" });
      }
      items.push({ ...base, kind: "approval", label: "Permission requested", status: "pending", name });
    } else if (event.type === "approval.decided" && (p.decision === "approved" || p.decision === "denied")) {
      const approvedCall = approvals.get(id(p.approval_id) ?? "");
      const tool = approvedCall ? tools.get(approvedCall) : undefined;
      if (tool) tool.permission = p.decision;
      items.push({ ...base, kind: "approval", label: p.decision === "approved" ? "Permission granted" : "Permission denied", status: p.decision, name: tool?.name });
    } else if (event.type === "input.required" || event.type === "input.answered") {
      items.push({ ...base, kind: "input", label: event.type === "input.required" ? "Your input requested" : p.skip === true ? "Your input skipped" : "Your input received", status: event.type === "input.required" ? "pending" : "recorded" });
    } else if (event.type === "artifact.updated") {
      const artifact = ownedArtifact(run, event);
      if (!artifact) continue;
      items.push({ ...base, kind: "artifact", label: "Deliverable saved", status: "recorded", name: text(artifact.title) });
    } else if (event.type === "citation.created") {
      const citationId = id(p.citation_id) ?? event.id;
      if (citationIds.has(citationId)) continue;
      citationIds.add(citationId);
      const meta = p.metadata && typeof p.metadata === "object" ? p.metadata as Record<string, unknown> : {};
      const url = safeUrl(p.locator) ?? (meta.kind === "knowledge" ? safeUrl(meta.source_uri) : null);
      const title = text(p.label) ?? text(p.source) ?? "Source";
      const documentId = id(meta.document_id);
      const pageNumber = nonnegativeInteger(meta.page_number) ?? nonnegativeInteger(meta.page);
      const chunkIndex = nonnegativeInteger(meta.chunk_index);
      citations.push({ id: citationId, title, url,
        ...(documentId !== undefined ? { documentId } : {}),
        ...(pageNumber !== undefined ? { pageNumber } : {}),
        ...(chunkIndex !== undefined ? { chunkIndex } : {}),
      });
      items.push({ ...base, kind: "citation", label: "Source recorded", status: "recorded", name: title });
    }
  }
  // A cancellation or terminal run without a call receipt proves no outcome.
  for (const tool of tools.values()) if (tool.status === "pending" && (terminal.has(run.status) || run.status === "cancel_requested")) tool.status = "unknown";
  return { items, tools: [...tools.values()], citations };
}

function markdown(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/[\\`*_{}\[\]()<>#!|~]/g, "\\$&");
}
function stamp(value: string | null | undefined, unknown: string): string {
  const date = value ? Date.parse(value) : NaN;
  return Number.isFinite(date) ? new Date(date).toISOString() : unknown;
}

/** Portable receipt, never a transcript or raw execution trace. */
export function exportRunReceiptMarkdown(run: Run, events: readonly ExecutionEvent[], translator: (text: string) => string = (value) => value): string {
  const receipt = buildRunReceipt(run, events);
  const duration = runDurationMs(run);
  const tr = (value: string) => markdown(translator(value));
  const lines = [`# ${tr("Work receipt")}`, "", `${tr("Title")}: ${text(run.title) ? markdown(text(run.title)!) : tr("Untitled work")}`, `${tr("Run ID")}: ${markdown(run.id)}`, `${tr("Thread ID")}: ${markdown(run.thread_id)}`, `${tr("Status")}: ${tr(run.status)}`,
    `${tr("Started")}: ${stamp(run.created_at, tr("Unknown"))}`, `${tr("Finished")}: ${stamp(run.completed_at, tr("Unknown"))}`, `${tr("Duration")}: ${duration === null ? tr("Unknown") : `${duration / 1000}${tr("s")}`}`, "", `## ${tr("Tool outcomes")}`, ""];
  const owned = ownedEvents(run, events);
  for (const tool of receipt.tools) {
    const named = owned.some((event) => event.payload.call_id === tool.callId && (text(event.payload.tool) || text(event.payload.name)));
    lines.push(`- ${named ? markdown(tool.name) : tr("Tool")}: ${tr(tool.status)}${tool.permission ? ` (${tr("permission")}: ${tr(tool.permission)})` : ""}`);
  }
  if (!receipt.tools.length) lines.push(tr("No tool outcomes recorded."));
  lines.push("", `## ${tr("Sources")}`, "");
  for (const citation of receipt.citations) {
    const named = owned.some((event) => event.type === "citation.created" && (id(event.payload.citation_id) ?? event.id) === citation.id && (text(event.payload.label) || text(event.payload.source)));
    const locators = [`${tr("Citation ID")}: ${markdown(citation.id)}`];
    if (citation.documentId !== undefined) locators.push(`${tr("Document ID")}: ${markdown(citation.documentId)}`);
    if (citation.pageNumber !== undefined) locators.push(`${tr("Page")}: ${citation.pageNumber}`);
    if (citation.chunkIndex !== undefined) locators.push(`${tr("Chunk index")}: ${citation.chunkIndex}`);
    lines.push(`- ${named ? markdown(citation.title) : tr("Source")}${citation.url ? ` — [${tr("Open source")}](<${citation.url.replace(/[<>\s]/g, (character) => encodeURIComponent(character))}>)` : ""} (${locators.join("; ")})`);
  }
  if (!receipt.citations.length) lines.push(tr("No sources recorded."));
  return lines.join("\n") + "\n";
}
