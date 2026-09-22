import type { ExecutionEvent } from "@alcuin/contracts";

/** Execution wall time excluding human clarification and approval waits. */
export function activeRunDuration(events: ExecutionEvent[], now?: number): number {
  const boundaries = events.filter((event) => ["run.started", "run.completed", "run.failed", "input.required", "input.answered", "approval.required", "approval.decided"].includes(event.type)).sort((a, b) => a.sequence - b.sequence);
  const start = boundaries.find((event) => event.type === "run.started");
  if (!start) return 0;
  const startMs = Date.parse(start.timestamp);
  const terminal = boundaries.find((event) => event.type === "run.completed" || event.type === "run.failed");
  const endMs = terminal ? Date.parse(terminal.timestamp) : now ?? Date.parse(boundaries.at(-1)!.timestamp);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return 0;
  const waiting = new Set<string>();
  let pausedAt: number | null = null;
  let pausedMs = 0;
  for (const event of boundaries) {
    const stamp = Math.min(endMs, Math.max(startMs, Date.parse(event.timestamp)));
    if (!Number.isFinite(stamp)) continue;
    const key = event.type.startsWith("input.") ? `input:${event.payload.input_id}` : `approval:${event.payload.approval_id}`;
    if (event.type === "input.required" || event.type === "approval.required") {
      if (waiting.size === 0) pausedAt = stamp;
      waiting.add(key);
    } else if (event.type === "input.answered" || event.type === "approval.decided") {
      if (!waiting.delete(key)) continue;
      if (waiting.size === 0 && pausedAt !== null) { pausedMs += Math.max(0, stamp - pausedAt); pausedAt = null; }
    }
  }
  if (pausedAt !== null) pausedMs += Math.max(0, endMs - pausedAt);
  return Math.max(0, endMs - startMs - pausedMs) / 1000;
}
