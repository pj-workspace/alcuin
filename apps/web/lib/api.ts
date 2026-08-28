import type {
  Agent,
  BootstrapPayload,
  ExecutionEvent,
  Extension,
  ExtensionInspection,
  ImageAttachment,
  KnowledgeDocument,
  KnowledgeSource,
  Run,
  Thread,
} from "@alcuin/contracts";

import { parseSseBuffer } from "@/lib/sse";

const API_URL = process.env.NEXT_PUBLIC_ALCUIN_API_URL ?? "http://localhost:8000";
const WORKSPACE_ID = process.env.NEXT_PUBLIC_ALCUIN_WORKSPACE_ID ?? "ws_demo";

const headers = { "Content-Type": "application/json", "X-Alcuin-Workspace": WORKSPACE_ID };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers, ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : `Alcuin API returned ${response.status}`,
    );
  }
  return response.json();
}

export const alcuinApi = {
  bootstrap: () => request<BootstrapPayload>("/v1/bootstrap"),
  getRun: (runId: string) => request<Run & { events: ExecutionEvent[] }>(`/v1/runs/${runId}`),
  createThread: (agentId: string, context: Record<string, unknown> = {}) =>
    request<Thread>("/v1/threads", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId, title: "Operations review", context }),
    }),
  createRun: (
    threadId: string,
    input: string,
    attachments: ImageAttachment[] = [],
    thinking = true,
  ) =>
    request<Run>(`/v1/threads/${threadId}/runs`, {
      method: "POST",
      body: JSON.stringify({ input, attachments, thinking }),
    }),
  decideApproval: (runId: string, approvalId: string, decision: "approved" | "denied") =>
    request(`/v1/runs/${runId}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  saveAgent: (agentId: string, definition: Agent["definition"]) =>
    request<Agent>(`/v1/agents/${agentId}/versions`, {
      method: "POST",
      body: JSON.stringify({ definition }),
    }),
  publishAgent: (agentId: string) => request<Agent>(`/v1/agents/${agentId}/publish`, { method: "POST" }),
  createKnowledgeSource: (name: string, description: string) =>
    request<KnowledgeSource>("/v1/knowledge/sources", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  ingestKnowledgeDocument: (
    sourceId: string,
    document: { title: string; content: string; source_uri?: string; metadata?: Record<string, unknown> },
  ) =>
    request<{ document: KnowledgeDocument; indexed: boolean }>(
      `/v1/knowledge/sources/${sourceId}/documents`,
      { method: "POST", body: JSON.stringify(document) },
    ),
  healthExtension: (extensionId: string) =>
    request<{ status: string; details: Record<string, unknown> }>(`/v1/extensions/${extensionId}/health`, {
      method: "POST",
    }),
  setExtension: (extensionId: string, enabled: boolean) =>
    request<Extension>(`/v1/extensions/${extensionId}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  inspectSampleMcp: () =>
    request<ExtensionInspection>("/v1/extensions/inspect", {
      method: "POST",
      body: JSON.stringify({
        manifest: {
          manifest_version: "1",
          id: "northstar.docs",
          name: "Northstar Knowledge",
          version: "0.1.0",
          description: "Search internal runbooks through MCP.",
          compatibility: ">=0.1.0",
          contributions: { tools: [], skills: [], agent_templates: [], knowledge_connectors: [], ui_blocks: [] },
          entrypoints: [{ type: "mcp", transport: "stdio", command: "python3", args: ["-m", "northstar_mcp"] }],
          config_schema: { type: "object" },
          permissions: [{ id: "docs:read", reason: "Search operational runbooks", risk: "low", required: true }],
          credential_requirements: [],
        },
      }),
    }),
  createEmbedSession: (agentId: string) =>
    request<{ token: string; expires_at: number; agent_version_id: string; origin: string }>("/v1/embed/sessions", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId, origin: "http://localhost:3000" }),
    }),
  streamRun: async (
    runId: string,
    onEvent: (event: ExecutionEvent) => void,
    after = 0,
  ) => {
    const response = await fetch(`${API_URL}/v1/runs/${runId}/events?after=${after}&protocol=tcm`, { headers });
    if (!response.ok || !response.body) throw new Error("Unable to stream run events");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamDone = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseBuffer(buffer);
      buffer = parsed.remainder;
      for (const frame of parsed.frames) {
        if (frame.kind === "done") {
          streamDone = true;
          break;
        }
        if (frame.kind === "event") {
          const event = executionEventFromTcmFrame(frame.value);
          if (event) onEvent(event);
        }
      }
      if (streamDone) break;
    }
  },
};

function executionEventFromTcmFrame(value: unknown): ExecutionEvent | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const base = {
    id: String(row.eventId ?? `${row.runId ?? "run"}-${row.sequence ?? 0}`),
    run_id: String(row.runId ?? ""),
    sequence: Number(row.sequence ?? 0),
    timestamp: String(row.timestamp ?? new Date().toISOString()),
  };
  if (row.type === "meta") return { ...base, type: "run.started", payload: {
    provider: row.provider,
    model: row.chatModel,
    runtime: row.runtime,
    thinking: row.thinking,
    input_modalities: row.inputModalities,
    attachment_count: row.attachmentCount,
  } };
  if (row.type === "thinking-delta") return { ...base, type: "reasoning.delta", payload: { delta: row.textDelta } };
  if (row.type === "text-delta") return { ...base, type: "message.delta", payload: { delta: row.textDelta } };
  if (row.type === "tool-call") return { ...base, type: "tool.requested", payload: {
    tool: row.name,
    arguments: row.input,
    summary: row.summary,
    mutating: row.mutating,
  } };
  if (row.type === "tool-result") return { ...base, type: "tool.completed", payload: {
    tool: row.name,
    status: row.status === "success" ? "succeeded" : row.status,
    result_summary: row.outputPreview,
  } };
  if (row.type === "approval-required") {
    const { type: _type, eventId: _eventId, runId: _runId, sequence: _sequence, timestamp: _timestamp, ...payload } = row;
    void _type; void _eventId; void _runId; void _sequence; void _timestamp;
    return { ...base, type: "approval.required", payload };
  }
  if (row.type === "artifact-updated") return { ...base, type: "artifact.updated", payload: { artifact: row.artifact } };
  if (row.type === "source-registry") return { ...base, type: "citation.created", payload: Array.isArray(row.sources) ? row.sources[0] ?? {} : {} };
  if (row.type === "error") return { ...base, type: "run.failed", payload: { message: row.message } };
  if (row.type === "done") return { ...base, type: "run.completed", payload: { status: "completed" } };
  return null;
}

export { API_URL, WORKSPACE_ID };
