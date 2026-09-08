import {
  initialTaskProjection,
  projectTaskEvents,
  type TaskProjection,
} from "./task-projection.ts";
import type { Task, TaskCommandName, TaskEvent, TaskStep } from "@alcuin/contracts";

export type PendingTaskCommand = {
  id: string;
  command: TaskCommandName;
  label?: string;
};

export type TaskSessionPhase = "idle" | "loading" | "ready" | "reconnecting" | "error";

export type TaskPlanDraft = {
  goal: string;
  steps: Array<Pick<TaskStep, "id" | "title" | "description" | "ordinal">>;
};

export type TaskSessionState = {
  phase: TaskSessionPhase;
  projection: TaskProjection;
  selectedStepId: string | null;
  planDraft: TaskPlanDraft | null;
  planSaving: boolean;
  pendingCommands: PendingTaskCommand[];
  error: string | null;
};

export type TaskSessionAction =
  | { type: "reset" }
  | { type: "hydrate.started" }
  | { type: "hydrate.completed"; task: Task | null; events?: TaskEvent[] }
  | { type: "hydrate.failed"; message: string }
  | { type: "snapshot.received"; task: Task }
  | { type: "stream.disconnected"; message?: string }
  | { type: "events.received"; events: TaskEvent[] }
  | { type: "step.selected"; stepId: string | null }
  | { type: "plan.edit.started" }
  | { type: "plan.edit.cancelled" }
  | { type: "plan.edit.changed"; draft: TaskPlanDraft }
  | { type: "plan.save.started" }
  | { type: "plan.save.failed"; message: string }
  | { type: "plan.save.completed"; task: Task; events?: TaskEvent[] }
  | { type: "command.requested"; pending: PendingTaskCommand }
  | { type: "command.acknowledged"; commandId: string; events?: TaskEvent[] }
  | { type: "command.failed"; commandId: string; message: string }
  | { type: "error.cleared" };

export const initialTaskSessionState: TaskSessionState = {
  phase: "idle",
  projection: initialTaskProjection(),
  selectedStepId: null,
  planDraft: null,
  planSaving: false,
  pendingCommands: [],
  error: null,
};

export function taskSessionReducer(
  state: TaskSessionState,
  action: TaskSessionAction,
): TaskSessionState {
  switch (action.type) {
    case "reset":
      return initialTaskSessionState;
    case "hydrate.started":
      return { ...state, phase: "loading", error: null };
    case "hydrate.completed": {
      const projection = projectTaskEvents(initialTaskProjection(action.task), action.events ?? []);
      return {
        ...initialTaskSessionState,
        phase: "ready",
        projection,
        selectedStepId: selectedStepAfterProjection(null, projection),
      };
    }
    case "hydrate.failed":
      return { ...state, phase: "error", error: action.message };
    case "snapshot.received": {
      const projection = projectTaskEvents(initialTaskProjection(action.task), state.projection.events);
      return {
        ...state,
        phase: "ready",
        projection,
        selectedStepId: selectedStepAfterProjection(state.selectedStepId, projection),
        error: null,
      };
    }
    case "stream.disconnected":
      return { ...state, phase: "reconnecting", error: action.message ?? null };
    case "events.received": {
      const projection = projectTaskEvents(state.projection, action.events);
      return {
        ...state,
        phase: "ready",
        projection,
        selectedStepId: selectedStepAfterProjection(state.selectedStepId, projection),
        error: null,
      };
    }
    case "step.selected":
      return {
        ...state,
        selectedStepId: action.stepId && state.projection.task?.plan.steps.some((step) => step.id === action.stepId)
          ? action.stepId
          : null,
      };
    case "plan.edit.started":
      return {
        ...state,
        planDraft: state.projection.task ? draftFromTask(state.projection.task) : null,
        error: null,
      };
    case "plan.edit.cancelled":
      return { ...state, planDraft: null, planSaving: false, error: null };
    case "plan.edit.changed":
      return { ...state, planDraft: normalizeDraft(action.draft), error: null };
    case "plan.save.started":
      return state.planDraft ? { ...state, planSaving: true, error: null } : state;
    case "plan.save.failed":
      return { ...state, planSaving: false, error: action.message };
    case "plan.save.completed": {
      const projection = projectTaskEvents(
        initialTaskProjection(action.task),
        action.events ?? state.projection.events,
      );
      return {
        ...state,
        projection,
        selectedStepId: selectedStepAfterProjection(state.selectedStepId, projection),
        planDraft: null,
        planSaving: false,
        error: null,
      };
    }
    case "command.requested":
      if (state.pendingCommands.some((item) => item.id === action.pending.id)) return state;
      return {
        ...state,
        pendingCommands: [...state.pendingCommands, action.pending],
        error: null,
      };
    case "command.acknowledged":
      return {
        ...state,
        projection: action.events ? projectTaskEvents(state.projection, action.events) : state.projection,
        pendingCommands: state.pendingCommands.filter((item) => item.id !== action.commandId),
        error: null,
      };
    case "command.failed":
      return {
        ...state,
        pendingCommands: state.pendingCommands.filter((item) => item.id !== action.commandId),
        error: action.message,
      };
    case "error.cleared":
      return { ...state, error: null };
  }
}

export function draftFromTask(task: Task): TaskPlanDraft {
  return {
    goal: task.goal,
    steps: task.plan.steps.map((step, index) => ({
      id: step.id,
      title: step.title,
      description: step.description ?? "",
      ordinal: index,
    })),
  };
}

export function taskHasPendingCommand(
  state: Pick<TaskSessionState, "pendingCommands">,
  command: TaskCommandName,
): boolean {
  return state.pendingCommands.some((item) => item.command === command);
}

function selectedStepAfterProjection(
  selectedStepId: string | null,
  projection: TaskProjection,
): string | null {
  const task = projection.task;
  if (!task) return null;
  if (selectedStepId && task.plan.steps.some((step) => step.id === selectedStepId)) return selectedStepId;
  return task.current_step_id
    ?? task.plan.steps.find((step) => step.status === "running" || step.status.startsWith("waiting_"))?.id
    ?? task.plan.steps[0]?.id
    ?? null;
}

function normalizeDraft(draft: TaskPlanDraft): TaskPlanDraft {
  return {
    goal: draft.goal,
    steps: draft.steps.map((step, index) => ({
      id: step.id,
      title: step.title,
      description: step.description ?? "",
      ordinal: index,
    })),
  };
}
