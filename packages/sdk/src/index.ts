import type {
  Agent,
  BootstrapPayload,
  ContextAssembly,
  ExecutionEvent,
  Extension,
  ExtensionInspection,
  ImageAttachment,
  KnowledgeDocument,
  KnowledgeSource,
  Run,
  Thread,
  ThreadDetail,
  ThreadMessage,
} from "@alcuin/contracts";
import { SseProtocolError, streamJsonSse } from "@alcuin/sse-client";

export interface AlcuinClientOptions {
  baseUrl: string;
  workspaceId: string;
  fetch?: typeof fetch;
}

export interface ListThreadMessagesOptions {
  /** Return only messages after this Thread-local sequence. */
  after?: number;
  limit?: number;
}

function resourceId(value: string): string {
  return encodeURIComponent(value);
}

function threadMessagesPath(
  threadId: string,
  options: ListThreadMessagesOptions = {},
): string {
  const search = new URLSearchParams();
  if (options.after !== undefined) search.set("after", String(options.after));
  if (options.limit !== undefined) search.set("limit", String(options.limit));
  const query = search.toString();
  return `/v1/threads/${resourceId(threadId)}/messages${query ? `?${query}` : ""}`;
}

export function createAlcuinClient(options: AlcuinClientOptions) {
  const baseUrl = options.baseUrl.replace(/\/$/, "");
  const workspaceId = options.workspaceId.trim();
  const fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  if (!/^https?:\/\//.test(baseUrl)) throw new Error("Alcuin baseUrl must use http or https");
  if (!workspaceId) throw new Error("Alcuin workspaceId is required");
  const headers = { "Content-Type": "application/json", "X-Alcuin-Workspace": workspaceId };

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetchImpl(`${baseUrl}${path}`, {
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

  return {
  bootstrap: () => request<BootstrapPayload>("/v1/bootstrap"),
  getRun: (runId: string) => request<Run & { events: ExecutionEvent[] }>(`/v1/runs/${runId}`),
  getThread: (threadId: string) =>
    request<ThreadDetail>(`/v1/threads/${resourceId(threadId)}`),
  listThreadMessages: (
    threadId: string,
    options: ListThreadMessagesOptions = {},
  ) => request<ThreadMessage[]>(threadMessagesPath(threadId, options)),
  getRunContext: (runId: string) =>
    request<ContextAssembly>(`/v1/runs/${resourceId(runId)}/context`),
  createThread: (agentId: string, context: Record<string, unknown> = {}) =>
    request<Thread>("/v1/threads", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId, title: "Working session", context }),
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
  createAgent: (slug: string, definition: Agent["definition"]) =>
    request<Agent>("/v1/agents", {
      method: "POST",
      body: JSON.stringify({ slug, definition }),
    }),
  createToolRun: (
    threadId: string,
    input: string,
    name: string,
    arguments_: Record<string, unknown>,
    extensionManifestId: string,
    uiBlockId: string,
  ) =>
    request<Run>(`/v1/threads/${threadId}/runs`, {
      method: "POST",
      body: JSON.stringify({
        input,
        thinking: false,
        requested_tool: {
          name,
          arguments: arguments_,
          extension_manifest_id: extensionManifestId,
          ui_block_id: uiBlockId,
        },
      }),
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
  uploadKnowledgeFile: async (sourceId: string, file: File, title?: string) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (title?.trim()) form.append("title", title.trim());
    const response = await fetchImpl(`${baseUrl}/v1/knowledge/sources/${sourceId}/files`, {
      method: "POST",
      headers: { "X-Alcuin-Workspace": workspaceId },
      body: form,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = body.detail;
      throw new Error(typeof detail === "string" ? detail : `Alcuin API returned ${response.status}`);
    }
    return response.json() as Promise<{
      document: KnowledgeDocument;
      indexed: boolean;
      parsed: { filename: string; kind: string; characters: number };
    }>;
  },
  healthExtension: (extensionId: string) =>
    request<{ status: string; details: Record<string, unknown> }>(`/v1/extensions/${extensionId}/health`, {
      method: "POST",
    }),
  inspectExtensionManifest: (manifest: Record<string, unknown>) =>
    request<ExtensionInspection>("/v1/extensions/inspect", {
      method: "POST",
      body: JSON.stringify({ manifest }),
    }),
  importMcpExtension: (payload: {
    name: string;
    extension_id: string;
    version?: string;
    description?: string;
    selected_tools?: string[];
    entrypoint: Record<string, unknown>;
  }) => request<ExtensionInspection>("/v1/extensions/import/mcp", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  importOpenApiExtension: (payload: {
    name: string;
    extension_id: string;
    spec_text?: string;
    spec_url?: string;
    base_url?: string;
    selected_operations?: string[];
    auth: "none" | "api_key" | "bearer";
  }) => request<ExtensionInspection>("/v1/extensions/import/openapi", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  installExtension: (manifest: ExtensionInspection["manifest"]) =>
    request<Extension>("/v1/extensions", {
      method: "POST",
      body: JSON.stringify({ manifest, credential_refs: {} }),
    }),
  bindExtensionCredentials: (extensionId: string, credentialRefs: Record<string, string>) =>
    request<Extension>(`/v1/extensions/${extensionId}/credentials`, {
      method: "PATCH",
      body: JSON.stringify({ credential_refs: credentialRefs }),
    }),
  setExtension: (extensionId: string, enabled: boolean) =>
    request<Extension>(`/v1/extensions/${extensionId}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  createEmbedSession: (agentId: string, origin: string) =>
    request<{ token: string; expires_at: number; agent_version_id: string; origin: string }>("/v1/embed/sessions", {
      method: "POST",
      body: JSON.stringify({
        agent_id: agentId,
        origin,
        allowed_actions: ["thread:create", "run:create", "run:read", "approval:decide"],
      }),
    }),
  streamRun: async (
    runId: string,
    onEvent: (event: ExecutionEvent) => void,
    after = 0,
  ) => {
    await streamJsonSse({
      url: `${baseUrl}/v1/runs/${resourceId(runId)}/events`,
      headers,
      lastEventId: after,
      fetcher: fetchImpl,
      onEvent(value) {
        const event = executionEventFromCanonicalFrame(value);
        if (!event) throw new SseProtocolError("Canonical SSE frame is not an ExecutionEvent");
        onEvent(event);
      },
      isTerminal(value) {
        if (!value || typeof value !== "object") return false;
        return ["run.completed", "run.failed", "run.cancelled"].includes(
          String((value as Record<string, unknown>).type ?? ""),
        );
      },
    });
  },
  /** Compatibility stream for hosts that explicitly consume Alcuin chat frames. */
  streamRunChat: async (
    runId: string,
    onEvent: (event: ExecutionEvent) => void,
    after = 0,
  ) => {
    await streamJsonSse({
      url: `${baseUrl}/v1/runs/${resourceId(runId)}/events?protocol=chat`,
      headers,
      lastEventId: after,
      fetcher: fetchImpl,
      onEvent(value) {
        const event = executionEventFromChatFrame(value);
        if (event) onEvent(event);
      },
      isTerminal(value) {
        return Boolean(value && typeof value === "object" && (value as Record<string, unknown>).type === "done");
      },
    });
  },
  };
}

export type AlcuinClient = ReturnType<typeof createAlcuinClient>;

/**
 * Validate the stable event envelope without rejecting an event name introduced
 * by a newer Runtime adapter. Consumers can therefore retain and render unknown
 * trace events while upgrading their known vocabulary independently.
 */
export function executionEventFromCanonicalFrame(value: unknown): ExecutionEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (
    typeof row.id !== "string"
    || typeof row.run_id !== "string"
    || typeof row.type !== "string"
    || typeof row.timestamp !== "string"
    || typeof row.sequence !== "number"
    || !Number.isSafeInteger(row.sequence)
    || row.sequence < 0
    || !row.payload
    || typeof row.payload !== "object"
    || Array.isArray(row.payload)
  ) return null;
  return {
    id: row.id,
    run_id: row.run_id,
    sequence: row.sequence,
    type: row.type,
    timestamp: row.timestamp,
    payload: row.payload as Record<string, unknown>,
  };
}

export function executionEventFromChatFrame(value: unknown): ExecutionEvent | null {
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
