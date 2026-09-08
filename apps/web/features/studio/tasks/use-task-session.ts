"use client";

import type {
  Task,
  TaskCommandName,
  TaskCreate,
  TaskEvent,
  TaskStatus,
  Thread,
} from "@alcuin/contracts";
import { useCallback, useEffect, useReducer, useRef } from "react";

import { alcuinApi } from "@/shared/lib/api";
import { isTaskTerminal } from "./task-projection";
import {
  initialTaskSessionState,
  taskSessionReducer,
  type TaskPlanDraft,
  type TaskSessionAction,
} from "./task-session-reducer";

const TASK_RECONNECT_MS = 900;

type TaskControlCommand = Extract<TaskCommandName, "start" | "pause" | "resume" | "cancel" | "retry">;

export function useTaskSession({
  threadId,
  ensureThread,
}: {
  threadId: string | null;
  ensureThread: () => Promise<Thread>;
}) {
  const [state, dispatch] = useReducer(taskSessionReducer, initialTaskSessionState);
  const stateRef = useRef(state);
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const streamRef = useRef<{ taskId: string; controller: AbortController } | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const connectRef = useRef<(task: Task, after?: number) => void>(() => undefined);

  useEffect(() => { stateRef.current = state; }, [state]);

  const commit = useCallback((action: TaskSessionAction) => {
    stateRef.current = taskSessionReducer(stateRef.current, action);
    dispatch(action);
  }, []);

  const stopStream = useCallback(() => {
    streamRef.current?.controller.abort();
    streamRef.current = null;
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connect = useCallback((task: Task, after = 0) => {
    if (!mountedRef.current || isTaskTerminal(task.status)) return;
    if (streamRef.current?.taskId === task.id) return;
    stopStream();
    const controller = new AbortController();
    const generation = generationRef.current;
    streamRef.current = { taskId: task.id, controller };

    void (async () => {
      try {
        await alcuinApi.streamTask(
          task.id,
          (event: TaskEvent) => {
            if (!mountedRef.current || generation !== generationRef.current || controller.signal.aborted) return;
            commit({ type: "events.received", events: [event] });
          },
          after,
          controller.signal,
        );
        if (!mountedRef.current || generation !== generationRef.current || controller.signal.aborted) return;
        const snapshot = await alcuinApi.getTask(task.id);
        commit({ type: "snapshot.received", task: snapshot });
      } catch (error) {
        if (!mountedRef.current || generation !== generationRef.current || controller.signal.aborted || isAbortError(error)) return;
        const message = error instanceof Error ? error.message : "Task updates disconnected";
        commit({ type: "stream.disconnected", message });
        try {
          const snapshot = await alcuinApi.getTask(task.id);
          if (!mountedRef.current || generation !== generationRef.current) return;
          commit({ type: "snapshot.received", task: snapshot });
          if (!isTaskTerminal(snapshot.status)) {
            const cursor = stateRef.current.projection.lastSequence;
            streamRef.current = null;
            reconnectTimerRef.current = window.setTimeout(() => {
              reconnectTimerRef.current = null;
              connectRef.current(snapshot, cursor);
            }, TASK_RECONNECT_MS);
          }
        } catch {
          // Keep the reconnecting state visible; a user refresh will retry hydration.
        }
      } finally {
        if (streamRef.current?.controller === controller) streamRef.current = null;
      }
    })();
  }, [commit, stopStream]);

  useEffect(() => { connectRef.current = connect; }, [connect]);

  const hydrate = useCallback(async (requestedThreadId: string) => {
    const generation = ++generationRef.current;
    stopStream();
    commit({ type: "hydrate.started" });
    try {
      const tasks = await alcuinApi.listTasks({ threadId: requestedThreadId, limit: 50 });
      const latest = latestTaskForThread(tasks);
      const snapshot = latest ? await alcuinApi.getTask(latest.id) : null;
      if (!mountedRef.current || generation !== generationRef.current) return;
      commit({ type: "hydrate.completed", task: snapshot });
      if (snapshot && !isTaskTerminal(snapshot.status)) connect(snapshot, 0);
    } catch (error) {
      if (!mountedRef.current || generation !== generationRef.current) return;
      commit({
        type: "hydrate.failed",
        message: error instanceof Error ? error.message : "Unable to load Task",
      });
    }
  }, [commit, connect, stopStream]);

  useEffect(() => {
    mountedRef.current = true;
    if (!threadId) {
      generationRef.current += 1;
      stopStream();
      commit({ type: "reset" });
    } else {
      // Do not render the previous thread's Task while the new thread hydrates.
      commit({ type: "reset" });
      void hydrate(threadId);
    }
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      stopStream();
    };
  }, [commit, hydrate, stopStream, threadId]);

  const syncTask = useCallback((task: Task) => {
    commit({ type: "snapshot.received", task });
    if (!isTaskTerminal(task.status)) connect(task, stateRef.current.projection.lastSequence);
  }, [commit, connect]);

  const sendCommand = useCallback(async (
    command: TaskControlCommand,
    stepId?: string,
    message?: string,
  ) => {
    const task = stateRef.current.projection.task;
    if (!task) throw new Error("Task is unavailable");
    if (stateRef.current.pendingCommands.length > 0) throw new Error("A Task command is already in progress");
    const commandId = `task:${task.id}:${command}:${crypto.randomUUID()}`;
    commit({ type: "command.requested", pending: { id: commandId, command } });
    try {
      // Task Events intentionally do not expose CAS revisions. Refresh the
      // canonical resource at the command boundary so a long-running Task does
      // not issue controls with the revision from its initial snapshot.
      const canonical = await alcuinApi.getTask(task.id);
      commit({ type: "snapshot.received", task: canonical });
      const snapshot = await alcuinApi.commandTask(canonical.id, {
        command,
        idempotency_key: commandId,
        expected_revision: canonical.revision,
        expected_status: canonical.status,
        ...(stepId ? { step_id: stepId } : {}),
        ...(message?.trim() ? { message: message.trim() } : {}),
      });
      commit({ type: "snapshot.received", task: snapshot });
      commit({ type: "command.acknowledged", commandId });
      if (!isTaskTerminal(snapshot.status)) connect(snapshot, stateRef.current.projection.lastSequence);
      return snapshot;
    } catch (error) {
      try {
        const latest = await alcuinApi.getTask(task.id);
        commit({ type: "snapshot.received", task: latest });
      } catch {
        // The command error remains the most actionable message.
      }
      commit({
        type: "command.failed",
        commandId,
        message: error instanceof Error ? error.message : "Task command failed",
      });
      throw error;
    }
  }, [commit, connect]);

  const intervene = useCallback(async (message: string) => {
    const task = stateRef.current.projection.task;
    const value = message.trim();
    if (!task || !value) return null;
    if (stateRef.current.pendingCommands.length > 0) throw new Error("A Task command is already in progress");
    const command = interventionCommandForStatus(task.status);
    if (!command) throw new Error("This Task cannot accept guidance at its current boundary");
    const commandId = `task:${task.id}:${command}:${crypto.randomUUID()}`;
    commit({ type: "command.requested", pending: { id: commandId, command, label: value } });
    try {
      const canonical = await alcuinApi.getTask(task.id);
      commit({ type: "snapshot.received", task: canonical });
      const canonicalCommand = interventionCommandForStatus(canonical.status);
      if (!canonicalCommand) throw new Error("This Task cannot accept guidance at its current boundary");
      const snapshot = await alcuinApi.commandTask(canonical.id, {
        command: canonicalCommand,
        idempotency_key: commandId,
        expected_revision: canonical.revision,
        expected_status: canonical.status,
        message: value,
      });
      commit({ type: "snapshot.received", task: snapshot });
      commit({ type: "command.acknowledged", commandId });
      return snapshot;
    } catch (error) {
      try {
        commit({ type: "snapshot.received", task: await alcuinApi.getTask(task.id) });
      } catch {
        // Preserve the original intervention failure.
      }
      commit({
        type: "command.failed",
        commandId,
        message: error instanceof Error ? error.message : "Unable to guide Task",
      });
      throw error;
    }
  }, [commit]);

  const createTask = useCallback(async (
    goal: string,
    autoStart = true,
    profile: Pick<TaskCreate, "model_override" | "reasoning_effort"> = {},
  ) => {
    const value = goal.trim();
    if (!value) throw new Error("Task goal is required");
    const thread = await ensureThread();
    const title = value.split(/\r?\n/, 1)[0]!.slice(0, 160);
    const created = await alcuinApi.createTask(thread.id, {
      goal: value,
      steps: [{ title, description: value }],
      ...profile,
    });
    generationRef.current += 1;
    stopStream();
    commit({ type: "hydrate.completed", task: created });
    connect(created, 0);
    if (!autoStart) return created;
    return sendCommand("start");
  }, [commit, connect, ensureThread, sendCommand, stopStream]);

  const beginPlanEdit = useCallback(() => commit({ type: "plan.edit.started" }), [commit]);
  const changePlanDraft = useCallback((draft: TaskPlanDraft) => commit({ type: "plan.edit.changed", draft }), [commit]);
  const cancelPlanEdit = useCallback(() => commit({ type: "plan.edit.cancelled" }), [commit]);
  const savePlan = useCallback(async () => {
    const task = stateRef.current.projection.task;
    const draft = stateRef.current.planDraft;
    if (!task || !draft) return null;
    commit({ type: "plan.save.started" });
    try {
      const canonical = await alcuinApi.getTask(task.id);
      commit({ type: "snapshot.received", task: canonical });
      const snapshot = await alcuinApi.updateTaskPlan(canonical.id, {
        expected_revision: canonical.revision,
        goal: draft.goal.trim(),
        steps: draft.steps.map((step) => ({
          title: step.title.trim(),
          ...(step.description.trim() ? { description: step.description.trim() } : {}),
        })),
      });
      commit({ type: "plan.save.completed", task: snapshot });
      return snapshot;
    } catch (error) {
      try {
        commit({ type: "snapshot.received", task: await alcuinApi.getTask(task.id) });
      } catch {
        // Keep the draft intact when refresh also fails.
      }
      commit({
        type: "plan.save.failed",
        message: error instanceof Error ? error.message : "Unable to save Task plan",
      });
      throw error;
    }
  }, [commit]);

  const refresh = useCallback(async () => {
    if (threadId) await hydrate(threadId);
  }, [hydrate, threadId]);

  const task = state.projection.task;
  const activeTask = task && !isTaskTerminal(task.status) ? task : null;
  return {
    state,
    task,
    activeTask,
    busy: state.phase === "loading" || state.planSaving || state.pendingCommands.length > 0,
    createTask,
    sendCommand,
    intervene,
    beginPlanEdit,
    changePlanDraft,
    cancelPlanEdit,
    savePlan,
    selectStep: (stepId: string) => commit({ type: "step.selected", stepId }),
    clearError: () => commit({ type: "error.cleared" }),
    refresh,
    syncTask,
  };
}

export function latestTaskForThread(tasks: readonly Task[]): Task | null {
  const ordered = [...tasks].sort((left, right) => {
    const byCreated = Date.parse(right.created_at) - Date.parse(left.created_at);
    return byCreated || Date.parse(right.updated_at) - Date.parse(left.updated_at) || right.id.localeCompare(left.id);
  });
  return ordered.find((task) => !isTaskTerminal(task.status)) ?? ordered[0] ?? null;
}

export function interventionCommandForStatus(status: TaskStatus): "steer" | "queue" | null {
  if (status === "running" || status === "waiting_for_approval" || status === "waiting_for_user") return "steer";
  if (status === "pause_requested" || status === "paused") return "queue";
  return null;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export type TaskSessionController = ReturnType<typeof useTaskSession>;
