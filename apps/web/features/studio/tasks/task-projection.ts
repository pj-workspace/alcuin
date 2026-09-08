import type {
  Task,
  TaskAttempt,
  TaskAttemptStatus,
  TaskCheckpoint,
  TaskEvent,
  TaskEventType,
  TaskEvidence,
  TaskIntervention,
  TaskPlan,
  TaskStatus,
  TaskStep,
  TaskStepStatus,
} from "@alcuin/contracts";

export type TaskProjectionRecord = Task & {
  /** Full replay history projected for the UI; the public Task keeps only latest_checkpoint. */
  checkpoints: TaskCheckpoint[];
  /** Cross-step evidence used by the task canvas. */
  evidence: TaskEvidence[];
};

export type TaskProgress = {
  completed: number;
  total: number;
  percent: number;
};

export type TaskProjection = {
  task: TaskProjectionRecord | null;
  events: TaskEvent[];
  lastSequence: number;
  terminalSequence: number | null;
  progress: TaskProgress;
  /** Immutable replay base from the latest server snapshot. */
  baseTask: TaskProjectionRecord | null;
};

const TASK_TERMINAL = new Set<TaskStatus>(["completed", "failed", "cancelled"]);
const STEP_TERMINAL = new Set<TaskStepStatus>(["completed", "failed", "skipped", "cancelled"]);

export function initialTaskProjection(baseTask: Task | TaskProjectionRecord | null = null): TaskProjection {
  const task = baseTask ? taskToProjectionRecord(baseTask) : null;
  return {
    task,
    events: [],
    lastSequence: 0,
    terminalSequence: task && TASK_TERMINAL.has(task.status) ? 0 : null,
    progress: progressOf(task),
    baseTask: task ? cloneTask(task) : null,
  };
}

/**
 * Merge an SSE page and replay from the immutable server snapshot. Event ids
 * make reconnects idempotent; sequence sorting makes late and overlapping pages
 * safe without allowing a terminal task or step to regress.
 */
export function projectTaskEvents(
  previous: TaskProjection,
  incoming: readonly TaskEvent[],
): TaskProjection {
  if (incoming.length === 0) return previous;
  const merged = new Map(previous.events.map((event) => [event.id, event]));
  const knownTaskId = previous.task?.id ?? previous.baseTask?.id;

  for (const event of incoming) {
    if (knownTaskId && event.task_id !== knownTaskId) continue;
    if (!Number.isSafeInteger(event.sequence) || event.sequence < 1) continue;
    if (!merged.has(event.id)) merged.set(event.id, event);
  }

  const events = [...merged.values()].sort(compareEvents);
  if (sameEventOrder(previous.events, events)) return previous;

  let task = previous.baseTask ? cloneTask(previous.baseTask) : null;
  let terminalSequence: number | null = null;
  let highestPercent = progressOf(task).percent;

  for (const event of events) {
    // Sequence is the durable ordering authority. Once a terminal transition is
    // observed, retain later frames for cursor advancement but never let them
    // mutate the terminal projection.
    if (terminalSequence !== null && event.sequence > terminalSequence) continue;
    task = applyTaskEvent(task, event);
    if (isTerminalTaskEvent(event.type)) {
      terminalSequence = event.sequence;
    }
    highestPercent = Math.max(highestPercent, progressOf(task).percent);
  }

  if (terminalSequence === null && task && TASK_TERMINAL.has(task.status)) {
    terminalSequence = previous.terminalSequence ?? 0;
  }

  const progress = progressOf(task);
  return {
    task,
    events,
    lastSequence: events.at(-1)?.sequence ?? 0,
    terminalSequence,
    progress: { ...progress, percent: Math.max(progress.percent, highestPercent) },
    baseTask: previous.baseTask,
  };
}

export function isTaskTerminal(status: TaskStatus): boolean {
  return TASK_TERMINAL.has(status);
}

export function isTaskWorking(status: TaskStatus): boolean {
  return status === "planning"
    || status === "running"
    || status === "pause_requested"
    || status === "cancel_requested";
}

export function isTaskWaiting(status: TaskStatus): boolean {
  return status === "waiting_for_approval" || status === "waiting_for_user";
}

function applyTaskEvent(current: TaskProjectionRecord | null, event: TaskEvent): TaskProjectionRecord | null {
  if (event.type === "task.created") {
    return current ?? taskFromCreatedEvent(event);
  }
  if (!current || current.id !== event.task_id) return current;

  let next = cloneTask(current);
  const taskStatus = statusForTaskEvent(event.type);
  if (taskStatus) next = withTaskStatus(next, taskStatus, event);

  if (event.type === "task.plan.updated") {
    const candidate = asRecord(event.payload.plan) ?? event.payload;
    next.plan = mergePlan(next.plan, candidate, event.timestamp);
    next.evidence = uniqueById(next.plan.steps.flatMap((step) => step.evidence));
  }
  if (
    event.type.startsWith("task.step.")
    || event.type === "task.waiting_for_approval"
    || event.type === "task.waiting_for_user"
  ) next = applyStepEvent(next, event);
  if (event.type.startsWith("task.attempt.")) next = applyAttemptEvent(next, event);

  if (event.type === "task.evidence.added") {
    const evidence = evidenceFromRecord(asRecord(event.payload.evidence) ?? event.payload, next.id, event.step_id, event.timestamp);
    if (evidence) next = appendEvidence(next, evidence);
  }
  if (event.type === "task.checkpoint.created") {
    const checkpoint = checkpointFromRecord(asRecord(event.payload.checkpoint) ?? event.payload, next, event.timestamp);
    if (checkpoint && !next.checkpoints.some((item) => item.id === checkpoint.id)) {
      next.checkpoints.push(checkpoint);
      next.latest_checkpoint = checkpoint;
    }
  }
  if (event.type.startsWith("task.intervention.")) {
    next = applyInterventionEvent(next, event);
  }

  next.updated_at = event.timestamp;
  return next;
}

function taskFromCreatedEvent(event: TaskEvent): TaskProjectionRecord | null {
  const raw = asRecord(event.payload.task) ?? event.payload;
  const id = optionalString(raw.id) ?? event.task_id;
  const workspaceId = optionalString(raw.workspace_id) ?? event.workspace_id;
  const threadId = optionalString(raw.thread_id);
  const goal = optionalString(raw.goal);
  if (!id || !workspaceId || !threadId || !goal) return null;
  const plan = planFromRecord(asRecord(raw.plan), id, event.timestamp);
  const latestCheckpoint = checkpointFromRecord(asRecord(raw.latest_checkpoint), {
    id,
    revision: finiteNumber(raw.revision) ?? 0,
  }, event.timestamp);

  return {
    id,
    workspace_id: workspaceId,
    thread_id: threadId,
    goal,
    status: taskStatus(raw.status) ?? "draft",
    plan,
    current_step_id: optionalString(raw.current_step_id),
    result: asRecord(raw.result),
    latest_checkpoint: latestCheckpoint,
    pending_intervention: interventionFromRecord(asRecord(raw.pending_intervention), id, event.timestamp),
    revision: finiteNumber(raw.revision) ?? 0,
    created_at: optionalString(raw.created_at) ?? event.timestamp,
    updated_at: optionalString(raw.updated_at) ?? event.timestamp,
    completed_at: optionalString(raw.completed_at),
    error: optionalString(raw.error),
    checkpoints: latestCheckpoint ? [latestCheckpoint] : [],
    evidence: uniqueById(plan.steps.flatMap((step) => step.evidence)),
  };
}

function applyStepEvent(task: TaskProjectionRecord, event: TaskEvent): TaskProjectionRecord {
  const raw = asRecord(event.payload.step) ?? event.payload;
  const stepId = event.step_id ?? optionalString(raw.id) ?? optionalString(raw.step_id);
  if (!stepId) return task;
  const index = task.plan.steps.findIndex((step) => step.id === stepId);
  if (index < 0) return task;

  const previous = task.plan.steps[index];
  const requested = stepStatus(raw.status) ?? statusForStepEvent(event.type);
  const status = requested && canMoveStep(previous.status, requested) ? requested : previous.status;
  const evidence = recordArray(raw.evidence)
    .map((item) => evidenceFromRecord(item, task.id, stepId, event.timestamp))
    .filter(isDefined);

  task.plan.steps[index] = {
    ...previous,
    title: optionalString(raw.title) ?? previous.title,
    description: optionalString(raw.description) ?? previous.description,
    ordinal: finiteNumber(raw.ordinal) ?? previous.ordinal,
    status,
    started_at: optionalString(raw.started_at) ?? (status === "running" ? previous.started_at ?? event.timestamp : previous.started_at),
    completed_at: optionalString(raw.completed_at) ?? (STEP_TERMINAL.has(status) ? previous.completed_at ?? event.timestamp : previous.completed_at),
    evidence: uniqueById([...previous.evidence, ...evidence]),
  };
  task.plan.steps.sort(compareSteps);
  task.evidence = uniqueById(task.plan.steps.flatMap((step) => step.evidence));
  if (status === "running" || status === "waiting_for_approval" || status === "waiting_for_user") {
    task.current_step_id = stepId;
  }
  return task;
}

function applyAttemptEvent(task: TaskProjectionRecord, event: TaskEvent): TaskProjectionRecord {
  const raw = asRecord(event.payload.attempt) ?? event.payload;
  const stepId = event.step_id ?? optionalString(raw.step_id);
  const attemptId = event.attempt_id ?? optionalString(raw.id);
  if (!stepId || !attemptId) return task;
  const step = task.plan.steps.find((item) => item.id === stepId);
  if (!step) return task;
  const index = step.attempts.findIndex((attempt) => attempt.id === attemptId);
  const previous = index >= 0 ? step.attempts[index] : null;
  const status = attemptStatus(raw.status) ?? statusForAttemptEvent(event.type);
  const attempt: TaskAttempt = {
    id: attemptId,
    task_id: task.id,
    step_id: stepId,
    run_id: optionalString(raw.run_id) ?? previous?.run_id,
    number: finiteNumber(raw.number) ?? previous?.number ?? step.attempts.length + 1,
    status: status ?? previous?.status ?? "queued",
    started_at: optionalString(raw.started_at) ?? previous?.started_at ?? (event.type === "task.attempt.started" ? event.timestamp : null),
    completed_at: optionalString(raw.completed_at) ?? previous?.completed_at ?? (event.type !== "task.attempt.started" ? event.timestamp : null),
    error: optionalString(raw.error) ?? optionalString(event.payload.message) ?? previous?.error,
  };
  if (index >= 0) step.attempts[index] = attempt;
  else step.attempts.push(attempt);
  return task;
}

function applyInterventionEvent(task: TaskProjectionRecord, event: TaskEvent): TaskProjectionRecord {
  const raw = asRecord(event.payload.intervention) ?? event.payload;
  const intervention = interventionFromRecord(raw, task.id, event.timestamp);
  if (!intervention) return task;
  if (
    event.type === "task.intervention.queued"
    || event.type === "task.intervention.steered"
    || event.type === "task.intervention.interrupt_requested"
  ) {
    task.pending_intervention = { ...intervention, status: "pending" };
  } else if (task.pending_intervention?.id === intervention.id) {
    task.pending_intervention = null;
  }
  return task;
}

function withTaskStatus(task: TaskProjectionRecord, requested: TaskStatus, event: TaskEvent): TaskProjectionRecord {
  if (TASK_TERMINAL.has(task.status) && requested !== task.status) return task;
  task.status = requested;
  if (requested === "failed") task.error = optionalString(event.payload.message) ?? task.error;
  if (requested === "completed") {
    task.result = asRecord(event.payload.result) ?? task.result;
    task.completed_at = task.completed_at ?? event.timestamp;
  }
  return task;
}

function appendEvidence(task: TaskProjectionRecord, evidence: TaskEvidence): TaskProjectionRecord {
  task.evidence = uniqueById([...task.evidence, evidence]);
  const step = task.plan.steps.find((item) => item.id === evidence.step_id);
  if (step) step.evidence = uniqueById([...step.evidence, evidence]);
  return task;
}

function mergePlan(current: TaskPlan, raw: Record<string, unknown>, timestamp: string): TaskPlan {
  const taskId = optionalString(raw.task_id) ?? current.task_id;
  const incoming = recordArray(raw.steps)
    .map((item, index) => stepFromRecord(item, taskId, index, timestamp))
    .filter(isDefined);
  const previous = new Map(current.steps.map((step) => [step.id, step]));
  const steps = incoming.map((step) => {
    const old = previous.get(step.id);
    if (!old) return step;
    return {
      ...old,
      ...step,
      status: canMoveStep(old.status, step.status) ? step.status : old.status,
      attempts: uniqueById([...old.attempts, ...step.attempts]),
      evidence: uniqueById([...old.evidence, ...step.evidence]),
      completed_at: old.completed_at ?? step.completed_at,
    };
  });
  return {
    id: optionalString(raw.id) ?? current.id,
    task_id: taskId,
    steps: (steps.length > 0 ? steps : current.steps).sort(compareSteps),
    updated_at: optionalString(raw.updated_at) ?? timestamp,
  };
}

function statusForTaskEvent(type: TaskEventType): TaskStatus | null {
  switch (type) {
    case "task.started":
    case "task.resumed": return "running";
    case "task.step.retry_requested": return "running";
    case "task.intervention.interrupt_requested": return "pause_requested";
    case "task.pause_requested": return "pause_requested";
    case "task.paused": return "paused";
    case "task.waiting_for_approval": return "waiting_for_approval";
    case "task.waiting_for_user": return "waiting_for_user";
    case "task.cancel_requested": return "cancel_requested";
    case "task.cancelled": return "cancelled";
    case "task.completed": return "completed";
    case "task.failed": return "failed";
    default: return null;
  }
}

function isTerminalTaskEvent(type: TaskEventType): boolean {
  return type === "task.completed" || type === "task.failed" || type === "task.cancelled";
}

function statusForStepEvent(type: TaskEventType): TaskStepStatus | null {
  switch (type) {
    case "task.step.started": return "running";
    case "task.step.retry_requested": return "running";
    case "task.waiting_for_approval": return "waiting_for_approval";
    case "task.waiting_for_user": return "waiting_for_user";
    case "task.step.completed": return "completed";
    case "task.step.failed": return "failed";
    case "task.step.skipped": return "skipped";
    default: return null;
  }
}

function statusForAttemptEvent(type: TaskEventType): TaskAttemptStatus | null {
  if (type === "task.attempt.started") return "running";
  if (type === "task.attempt.completed") return "completed";
  if (type === "task.attempt.failed") return "failed";
  return null;
}

function canMoveStep(from: TaskStepStatus, to: TaskStepStatus): boolean {
  if (from === to) return true;
  if (STEP_TERMINAL.has(from)) return false;
  if (from === "pending") return true;
  return to !== "pending";
}

function planFromRecord(raw: Record<string, unknown> | null, taskId: string, timestamp: string): TaskPlan {
  if (!raw) return { id: `${taskId}:plan`, task_id: taskId, steps: [], updated_at: timestamp };
  return {
    id: optionalString(raw.id) ?? `${taskId}:plan`,
    task_id: optionalString(raw.task_id) ?? taskId,
    steps: recordArray(raw.steps).map((item, index) => stepFromRecord(item, taskId, index, timestamp)).filter(isDefined).sort(compareSteps),
    updated_at: optionalString(raw.updated_at) ?? timestamp,
  };
}

function stepFromRecord(raw: Record<string, unknown>, taskId: string, index: number, timestamp: string): TaskStep | null {
  const id = optionalString(raw.id) ?? optionalString(raw.step_id);
  const title = optionalString(raw.title);
  if (!id || !title) return null;
  return {
    id,
    task_id: optionalString(raw.task_id) ?? taskId,
    ordinal: finiteNumber(raw.ordinal) ?? index,
    title,
    description: optionalString(raw.description) ?? "",
    status: stepStatus(raw.status) ?? "pending",
    attempts: recordArray(raw.attempts).map((item) => attemptFromRecord(item, taskId, id)).filter(isDefined),
    evidence: recordArray(raw.evidence).map((item) => evidenceFromRecord(item, taskId, id, timestamp)).filter(isDefined),
    started_at: optionalString(raw.started_at),
    completed_at: optionalString(raw.completed_at),
  };
}

function attemptFromRecord(raw: Record<string, unknown>, taskId: string, stepId: string): TaskAttempt | null {
  const id = optionalString(raw.id);
  if (!id) return null;
  return {
    id,
    task_id: optionalString(raw.task_id) ?? taskId,
    step_id: optionalString(raw.step_id) ?? stepId,
    run_id: optionalString(raw.run_id),
    number: finiteNumber(raw.number) ?? 1,
    status: attemptStatus(raw.status) ?? "queued",
    started_at: optionalString(raw.started_at),
    completed_at: optionalString(raw.completed_at),
    error: optionalString(raw.error),
  };
}

function evidenceFromRecord(
  raw: Record<string, unknown>,
  taskId: string,
  stepId: string | null | undefined,
  timestamp: string,
): TaskEvidence | null {
  const id = optionalString(raw.id);
  const label = optionalString(raw.label);
  const resolvedStepId = optionalString(raw.step_id) ?? stepId;
  if (!id || !label || !resolvedStepId) return null;
  return {
    id,
    task_id: optionalString(raw.task_id) ?? taskId,
    step_id: resolvedStepId,
    attempt_id: optionalString(raw.attempt_id),
    kind: evidenceKind(raw.kind) ?? "note",
    label,
    summary: optionalString(raw.summary) ?? "",
    resource_id: optionalString(raw.resource_id),
    source_uri: optionalString(raw.source_uri),
    created_at: optionalString(raw.created_at) ?? timestamp,
  };
}

function checkpointFromRecord(
  raw: Record<string, unknown> | null,
  task: Pick<Task, "id" | "revision">,
  timestamp: string,
): TaskCheckpoint | null {
  if (!raw) return null;
  const id = optionalString(raw.id);
  if (!id) return null;
  return {
    id,
    task_id: optionalString(raw.task_id) ?? task.id,
    sequence: finiteNumber(raw.sequence) ?? 0,
    task_revision: finiteNumber(raw.task_revision) ?? task.revision,
    after_step_id: optionalString(raw.after_step_id),
    next_step_id: optionalString(raw.next_step_id),
    completed_step_ids: stringArray(raw.completed_step_ids),
    created_at: optionalString(raw.created_at) ?? timestamp,
  };
}

function interventionFromRecord(raw: Record<string, unknown> | null, taskId: string, timestamp: string): TaskIntervention | null {
  if (!raw) return null;
  const id = optionalString(raw.id) ?? optionalString(raw.intervention_id);
  const kind = interventionKind(raw.kind);
  const message = optionalString(raw.message) ?? optionalString(raw.content);
  if (!id || !kind || !message) return null;
  return {
    id,
    task_id: optionalString(raw.task_id) ?? taskId,
    kind,
    status: interventionStatus(raw.status) ?? "pending",
    message,
    target_run_id: optionalString(raw.target_run_id),
    created_at: optionalString(raw.created_at) ?? timestamp,
    applied_at: optionalString(raw.applied_at),
  };
}

function progressOf(task: TaskProjectionRecord | null): TaskProgress {
  if (!task) return { completed: 0, total: 0, percent: 0 };
  const total = task.plan.steps.length;
  const completed = task.plan.steps.filter((step) => step.status === "completed" || step.status === "skipped").length;
  return { completed, total, percent: total === 0 ? 0 : Math.round((completed / total) * 100) };
}

function taskToProjectionRecord(task: Task | TaskProjectionRecord): TaskProjectionRecord {
  const projected = task as TaskProjectionRecord;
  return cloneTask({
    ...task,
    checkpoints: projected.checkpoints ?? (task.latest_checkpoint ? [task.latest_checkpoint] : []),
    evidence: projected.evidence ?? uniqueById(task.plan.steps.flatMap((step) => step.evidence)),
  });
}

function cloneTask(task: TaskProjectionRecord): TaskProjectionRecord {
  return {
    ...task,
    plan: {
      ...task.plan,
      steps: task.plan.steps.map((step) => ({
        ...step,
        attempts: step.attempts.map((attempt) => ({ ...attempt })),
        evidence: step.evidence.map((evidence) => ({ ...evidence })),
      })),
    },
    latest_checkpoint: task.latest_checkpoint ? { ...task.latest_checkpoint, completed_step_ids: [...task.latest_checkpoint.completed_step_ids] } : task.latest_checkpoint,
    pending_intervention: task.pending_intervention ? { ...task.pending_intervention } : task.pending_intervention,
    result: task.result ? { ...task.result } : task.result,
    checkpoints: task.checkpoints.map((checkpoint) => ({ ...checkpoint, completed_step_ids: [...checkpoint.completed_step_ids] })),
    evidence: task.evidence.map((evidence) => ({ ...evidence })),
  };
}

function compareEvents(left: TaskEvent, right: TaskEvent): number {
  return left.sequence - right.sequence || left.timestamp.localeCompare(right.timestamp) || left.id.localeCompare(right.id);
}

function compareSteps(left: TaskStep, right: TaskStep): number {
  return left.ordinal - right.ordinal || left.id.localeCompare(right.id);
}

function sameEventOrder(left: readonly TaskEvent[], right: readonly TaskEvent[]): boolean {
  return left.length === right.length && left.every((event, index) => event.id === right[index]?.id);
}

function uniqueById<T extends { id: string }>(items: readonly T[]): T[] {
  return [...new Map(items.map((item) => [item.id, item])).values()];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter(isDefined) : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function taskStatus(value: unknown): TaskStatus | null {
  return typeof value === "string" && [
    "draft", "planning", "ready", "running", "pause_requested", "paused", "waiting_for_approval",
    "waiting_for_user", "cancel_requested", "cancelled", "completed", "failed",
  ].includes(value) ? value as TaskStatus : null;
}

function stepStatus(value: unknown): TaskStepStatus | null {
  return typeof value === "string" && [
    "pending", "running", "waiting_for_approval", "waiting_for_user", "completed", "failed", "skipped", "cancelled",
  ].includes(value) ? value as TaskStepStatus : null;
}

function attemptStatus(value: unknown): TaskAttemptStatus | null {
  return typeof value === "string" && [
    "queued", "running", "waiting_for_approval", "waiting_for_user", "completed", "failed", "interrupted", "cancelled",
  ].includes(value) ? value as TaskAttemptStatus : null;
}

function evidenceKind(value: unknown): TaskEvidence["kind"] | null {
  return typeof value === "string" && ["artifact", "citation", "tool_result", "checkpoint", "note"].includes(value)
    ? value as TaskEvidence["kind"]
    : null;
}

function interventionKind(value: unknown): TaskIntervention["kind"] | null {
  return value === "queue" || value === "steer" || value === "interrupt" ? value : null;
}

function interventionStatus(value: unknown): TaskIntervention["status"] | null {
  return value === "pending" || value === "applied" || value === "rejected" ? value : null;
}

function isDefined<T>(value: T | null): value is T {
  return value !== null;
}
