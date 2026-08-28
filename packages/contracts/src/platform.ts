export const AGENT_SCHEMA_VERSION = "2026-08-28" as const;

export const EXECUTION_EVENT_TYPES = [
  "run.started",
  "reasoning.delta",
  "message.delta",
  "tool.requested",
  "tool.completed",
  "approval.required",
  "artifact.updated",
  "citation.created",
  "run.completed",
  "run.failed",
] as const;

export type ExecutionEventType = (typeof EXECUTION_EVENT_TYPES)[number];
