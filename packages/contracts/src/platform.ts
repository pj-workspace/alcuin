export const AGENT_SCHEMA_VERSION = "2026-08-28" as const;

export const EXECUTION_EVENT_TYPES = [
  "run.started",
  "context.compaction.started",
  "context.compaction.completed",
  "context.compaction.failed",
  "context.assembled",
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

export const TASK_STATUSES = [
  "draft",
  "planning",
  "ready",
  "running",
  "pause_requested",
  "paused",
  "waiting_for_approval",
  "waiting_for_user",
  "cancel_requested",
  "cancelled",
  "completed",
  "failed",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];

export type ReasoningEffort = "none" | "low" | "medium" | "high";

export const TASK_STEP_STATUSES = [
  "pending",
  "running",
  "waiting_for_approval",
  "waiting_for_user",
  "completed",
  "failed",
  "skipped",
  "cancelled",
] as const;

export type TaskStepStatus = (typeof TASK_STEP_STATUSES)[number];

export type TaskAttemptStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "waiting_for_user"
  | "completed"
  | "failed"
  | "interrupted"
  | "cancelled";

export type TaskEvidenceKind =
  | "artifact"
  | "citation"
  | "tool_result"
  | "checkpoint"
  | "note";

export type TaskInterventionKind = "queue" | "steer" | "interrupt";
export type TaskInterventionStatus = "pending" | "applied" | "rejected";
export type TaskCommandName =
  | "start"
  | "pause"
  | "resume"
  | "cancel"
  | "retry"
  | "queue"
  | "steer"
  | "interrupt";

export const TASK_EVENT_TYPES = [
  "task.created",
  "task.plan.updated",
  "task.started",
  "task.pause_requested",
  "task.paused",
  "task.resumed",
  "task.waiting_for_approval",
  "task.waiting_for_user",
  "task.cancel_requested",
  "task.cancelled",
  "task.completed",
  "task.failed",
  "task.step.started",
  "task.step.completed",
  "task.step.failed",
  "task.step.retry_requested",
  "task.step.skipped",
  "task.attempt.started",
  "task.attempt.completed",
  "task.attempt.failed",
  "task.checkpoint.created",
  "task.evidence.added",
  "task.intervention.queued",
  "task.intervention.steered",
  "task.intervention.interrupt_requested",
  "task.intervention.applied",
  "task.intervention.rejected",
] as const;

export type TaskEventType = (typeof TASK_EVENT_TYPES)[number];

export interface TaskStepDraft {
  title: string;
  description?: string;
}

export interface TaskCreate {
  goal: string;
  steps?: TaskStepDraft[];
  model_override?: string | null;
  reasoning_effort?: ReasoningEffort | null;
}

/** Planning is read-only: a proposal does not create or start a Task. */
export type TaskPlanProposalRequest = Pick<TaskCreate, "goal" | "model_override" | "reasoning_effort">;

export interface TaskPlanProposal {
  goal: string;
  steps: Array<{ title: string; description: string }>;
  model: string;
  reasoning_effort: ReasoningEffort | null;
}

export interface TaskPlanUpdate {
  /** Internal compare-and-swap guard, not a product version. */
  expected_revision: number;
  goal?: string | null;
  steps: TaskStepDraft[];
}

export interface TaskEvidence {
  id: string;
  task_id: string;
  step_id: string;
  attempt_id?: string | null;
  kind: TaskEvidenceKind;
  label: string;
  summary: string;
  resource_id?: string | null;
  source_uri?: string | null;
  created_at: string;
}

export interface TaskAttempt {
  id: string;
  task_id: string;
  step_id: string;
  run_id?: string | null;
  number: number;
  status: TaskAttemptStatus;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
}

export interface TaskStep {
  id: string;
  task_id: string;
  ordinal: number;
  title: string;
  description: string;
  status: TaskStepStatus;
  attempts: TaskAttempt[];
  evidence: TaskEvidence[];
  started_at?: string | null;
  completed_at?: string | null;
}

export interface TaskPlan {
  id: string;
  task_id: string;
  steps: TaskStep[];
  updated_at: string;
}

export interface TaskCheckpoint {
  id: string;
  task_id: string;
  sequence: number;
  /** Internal compare-and-swap snapshot, not a product version. */
  task_revision: number;
  after_step_id?: string | null;
  next_step_id?: string | null;
  completed_step_ids: string[];
  created_at: string;
}

export interface TaskIntervention {
  id: string;
  task_id: string;
  kind: TaskInterventionKind;
  status: TaskInterventionStatus;
  message: string;
  target_run_id?: string | null;
  created_at: string;
  applied_at?: string | null;
}

export interface TaskCommand {
  command: TaskCommandName;
  idempotency_key: string;
  /** Internal compare-and-swap guard, not a product version. */
  expected_revision: number;
  expected_status?: TaskStatus | null;
  step_id?: string | null;
  message?: string | null;
}

export interface Task {
  id: string;
  workspace_id: string;
  thread_id: string;
  goal: string;
  /** Persisted for every step, retry, and resume of this Task. */
  model_override?: string | null;
  reasoning_effort?: ReasoningEffort | null;
  status: TaskStatus;
  plan: TaskPlan;
  current_step_id?: string | null;
  result?: Record<string, unknown> | null;
  latest_checkpoint?: TaskCheckpoint | null;
  pending_intervention?: TaskIntervention | null;
  /** Internal compare-and-swap guard, not a product version. */
  revision: number;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  error?: string | null;
}

export interface TaskEvent {
  id: string;
  workspace_id: string;
  task_id: string;
  sequence: number;
  type: TaskEventType;
  timestamp: string;
  step_id?: string | null;
  attempt_id?: string | null;
  payload: Record<string, unknown>;
}

export const TERMINAL_TASK_STATUSES = ["cancelled", "completed", "failed"] as const;
export const COMPLETABLE_TASK_STEP_STATUSES = ["completed", "skipped"] as const;

export const TASK_STATUS_TRANSITIONS = {
  draft: ["planning", "ready", "cancel_requested"],
  planning: ["ready", "cancel_requested", "failed"],
  ready: ["planning", "running", "cancel_requested", "failed"],
  running: [
    "pause_requested",
    "waiting_for_approval",
    "waiting_for_user",
    "cancel_requested",
    "completed",
    "failed",
  ],
  pause_requested: ["paused", "cancel_requested", "failed"],
  paused: ["running", "cancel_requested", "failed"],
  waiting_for_approval: ["running", "pause_requested", "cancel_requested", "failed"],
  waiting_for_user: ["running", "pause_requested", "cancel_requested", "failed"],
  cancel_requested: ["cancelled", "failed"],
  cancelled: [],
  completed: [],
  failed: [],
} as const satisfies Record<TaskStatus, readonly TaskStatus[]>;

export function canCompleteTask(
  stepStatuses: readonly TaskStepStatus[],
  result: Readonly<Record<string, unknown>> | null | undefined,
): boolean {
  return Boolean(result && Object.keys(result).length > 0) && stepStatuses.every((status) =>
    (COMPLETABLE_TASK_STEP_STATUSES as readonly TaskStepStatus[]).includes(status),
  );
}

export function canTransitionTask(
  current: TaskStatus,
  target: TaskStatus,
  stepStatuses: readonly TaskStepStatus[] = [],
  result?: Readonly<Record<string, unknown>> | null,
): boolean {
  if (!(TASK_STATUS_TRANSITIONS[current] as readonly TaskStatus[]).includes(target)) {
    return false;
  }
  return target !== "completed" || canCompleteTask(stepStatuses, result);
}
