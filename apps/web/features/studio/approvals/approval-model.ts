import type { ExecutionEvent, RunStatus } from "@alcuin/contracts";

export type ApprovalDecision = "approved" | "denied";
export type ApprovalState = "pending" | "approved" | "denied" | "succeeded" | "failed" | "unavailable";
export type ApprovalRecord = {
  id: string;
  tool: string;
  title: string;
  description: string;
  arguments: Record<string, unknown>;
  state: ApprovalState;
  decision?: ApprovalDecision;
  note?: string;
  decidedAt?: string;
};

/** Sequence is the source of truth; never infer permission from Run completion. */
export function approvalRecords(events: ExecutionEvent[], runStatus: RunStatus | "submitting"): ApprovalRecord[] {
  const records = new Map<string, ApprovalRecord & { callId?: string }>();
  // Token deltas dominate long streams; don't copy/sort the entire transcript
  // on every render just to update a small number of human interactions.
  const interactions = events.filter((event) => ["approval.required", "approval.decided", "tool.completed"].includes(event.type));
  for (const event of interactions.sort((a, b) => a.sequence - b.sequence)) {
    const payload = event.payload;
    if (event.type === "approval.required" && typeof payload.approval_id === "string") {
      records.set(payload.approval_id, {
        id: payload.approval_id,
        tool: typeof payload.tool === "string" ? payload.tool : "",
        callId: typeof payload.call_id === "string" ? payload.call_id : undefined,
        title: typeof payload.title === "string" ? payload.title : "",
        description: typeof payload.description === "string" ? payload.description : "",
        arguments: payload.arguments && typeof payload.arguments === "object" && !Array.isArray(payload.arguments) ? payload.arguments : {},
        state: "pending",
      });
    } else if (event.type === "approval.decided") {
      const record = records.get(payload.approval_id);
      if (!record || !["approved", "denied"].includes(payload.decision)) continue;
      record.decision = payload.decision;
      record.state = payload.decision;
      record.note = typeof payload.note === "string" ? payload.note : undefined;
      record.decidedAt = typeof payload.decided_at === "string" ? payload.decided_at : event.timestamp;
    } else if (event.type === "tool.completed") {
      // A same-name call is not sufficient evidence: multiple invocations can
      // occur in a Run. Match the exact approved call, including legacy Runs.
      for (const record of records.values()) {
        if (!record.callId || record.callId !== payload.call_id) continue;
        if (payload.status === "succeeded") record.state = "succeeded";
        else if (payload.status === "failed") record.state = "failed";
        else if (payload.status === "denied") record.state = "denied";
      }
    }
  }
  const terminal = ["completed", "failed", "cancelled"].includes(runStatus);
  for (const record of records.values()) {
    if (record.state === "pending" && terminal) record.state = "unavailable";
    // Authorization survived but execution has no durable receipt; don't
    // show a success checkmark or allow the user to run it twice.
    if (record.state === "approved" && terminal) record.state = "unavailable";
  }
  return [...records.values()];
}

export function argumentFields(arguments_: Record<string, unknown>): { label: string; value: string }[] {
  return Object.entries(arguments_).map(([key, value]) => ({
    label: key.replace(/[_-]+/g, " "),
    value: typeof value === "string" ? value : JSON.stringify(value) ?? "",
  }));
}
