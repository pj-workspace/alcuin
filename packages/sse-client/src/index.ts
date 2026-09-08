export type ParsedSseFrame =
  | { kind: "done"; id?: string }
  | { kind: "event"; id?: string; event?: string; value: unknown }
  | { kind: "invalid"; id?: string; event?: string; value: string };

export interface StreamJsonSseOptions {
  url: string;
  headers?: HeadersInit;
  lastEventId?: number;
  onEvent: (value: unknown) => void | Promise<void>;
  isTerminal?: (value: unknown) => boolean;
  fetcher?: typeof fetch;
  maxReconnects?: number;
  reconnectDelayMs?: number;
  signal?: AbortSignal;
}

export interface StreamJsonSseResult {
  lastEventId: number;
  reconnects: number;
}

export class SseProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SseProtocolError";
  }
}

function parseChunk(chunk: string): ParsedSseFrame | null {
  let id: string | undefined;
  let event: string | undefined;
  const data: string[] = [];
  for (const rawLine of chunk.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id" && !value.includes("\0")) id = value;
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  const value = data.join("\n");
  if (value.trim() === "[DONE]") return { kind: "done", ...(id === undefined ? {} : { id }) };
  try {
    return {
      kind: "event",
      ...(id === undefined ? {} : { id }),
      ...(event === undefined ? {} : { event }),
      value: JSON.parse(value) as unknown,
    };
  } catch {
    return {
      kind: "invalid",
      ...(id === undefined ? {} : { id }),
      ...(event === undefined ? {} : { event }),
      value,
    };
  }
}

export function parseSseBuffer(buffer: string): {
  remainder: string;
  frames: ParsedSseFrame[];
} {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const chunks = normalized.split("\n\n");
  const remainder = chunks.pop() ?? "";
  const frames = chunks.flatMap((chunk) => {
    const frame = parseChunk(chunk);
    return frame ? [frame] : [];
  });
  return { remainder, frames };
}

function eventSequence(id: string | undefined): number | undefined {
  if (id === undefined) return undefined;
  if (!/^\d+$/.test(id)) throw new SseProtocolError("SSE event id must be a non-negative sequence");
  const sequence = Number(id);
  if (!Number.isSafeInteger(sequence)) throw new SseProtocolError("SSE event id exceeds the safe sequence range");
  return sequence;
}

function retryableStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

async function reconnectDelay(delayMs: number, attempt: number, signal?: AbortSignal) {
  const duration = Math.min(delayMs * 2 ** attempt, 2_000);
  if (duration <= 0) return;
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(resolve, duration);
    signal?.addEventListener("abort", () => {
      clearTimeout(timeout);
      reject(signal.reason);
    }, { once: true });
  });
}

export async function streamJsonSse(options: StreamJsonSseOptions): Promise<StreamJsonSseResult> {
  const fetcher = options.fetcher ?? fetch;
  const maxReconnects = options.maxReconnects ?? 4;
  const reconnectDelayMs = options.reconnectDelayMs ?? 250;
  let cursor = options.lastEventId ?? 0;
  let reconnects = 0;

  while (true) {
    options.signal?.throwIfAborted();
    const headers = new Headers(options.headers);
    if (cursor > 0) headers.set("Last-Event-ID", String(cursor));

    let response: Response;
    try {
      response = await fetcher(options.url, { headers, signal: options.signal });
    } catch (error) {
      if (options.signal?.aborted || reconnects >= maxReconnects) throw error;
      await reconnectDelay(reconnectDelayMs, reconnects++, options.signal);
      continue;
    }
    if (!response.ok || !response.body) {
      if (!retryableStatus(response.status) || reconnects >= maxReconnects) {
        throw new Error(`SSE request failed with status ${response.status}`);
      }
      await reconnectDelay(reconnectDelayMs, reconnects++, options.signal);
      continue;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let disconnected = false;
    while (true) {
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (error) {
        if (options.signal?.aborted || reconnects >= maxReconnects) throw error;
        disconnected = true;
        break;
      }
      if (result.done) {
        disconnected = true;
        break;
      }
      buffer += decoder.decode(result.value, { stream: true });
      const parsed = parseSseBuffer(buffer);
      buffer = parsed.remainder;
      for (const frame of parsed.frames) {
        if (frame.kind === "done") return { lastEventId: cursor, reconnects };
        if (frame.kind === "invalid") throw new SseProtocolError("SSE data frame is not valid JSON");
        const sequence = eventSequence(frame.id);
        if (sequence !== undefined && sequence <= cursor) continue;
        await options.onEvent(frame.value);
        if (sequence !== undefined) cursor = sequence;
        if (options.isTerminal?.(frame.value)) return { lastEventId: cursor, reconnects };
      }
    }

    if (!disconnected || reconnects >= maxReconnects) {
      throw new Error("SSE stream ended before a terminal event");
    }
    await reconnectDelay(reconnectDelayMs, reconnects++, options.signal);
  }
}
