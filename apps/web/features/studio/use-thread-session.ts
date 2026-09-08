"use client";

import type { Agent, AttachmentResource, ContextAssembly, ExecutionEvent, Run, Thread } from "@alcuin/contracts";
import type { CreateRunOptions } from "@alcuin/sdk";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import {
  initialThreadSessionState,
  optimisticTurn,
  threadSessionReducer,
  type ThreadDetailShape,
  type ThreadSessionState,
} from "@/features/studio/thread-session-reducer";

type SendOptions = {
  onAccepted?: () => void;
  runOptions?: CreateRunOptions;
};

type ToolRunInput = {
  label: string;
  tool: string;
  arguments: Record<string, unknown>;
  extensionManifestId: string;
  uiBlockId: string;
};

export function useThreadSession({
  agent,
  threadId,
  hostContext,
  onThreadCreated,
  onRunCreated,
}: {
  agent?: Agent;
  threadId: string | null;
  hostContext: Record<string, unknown>;
  onThreadCreated: (thread: Thread) => void;
  onRunCreated: (runId: string) => Promise<void>;
}) {
  const [state, dispatch] = useReducer(threadSessionReducer, initialThreadSessionState);
  const [contextAssembly, setContextAssembly] = useState<ContextAssembly | null>(null);
  const stateRef = useRef(state);
  const loadGenerationRef = useRef(0);
  const connectedRunRef = useRef<string | null>(null);
  const contextRunRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const resumeRunRef = useRef<(run: Run, after: number) => void>(() => undefined);
  const eventQueueRef = useRef(new Map<string, ExecutionEvent[]>());
  const frameRef = useRef<number | null>(null);

  useEffect(() => { stateRef.current = state; }, [state]);

  const flushEvents = useCallback(() => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    const batches = [...eventQueueRef.current.entries()];
    eventQueueRef.current.clear();
    for (const [runId, events] of batches) {
      dispatch({ type: "events.appended", runId, events });
    }
  }, []);

  const queueEvent = useCallback((runId: string, event: ExecutionEvent) => {
    if (!mountedRef.current) return;
    const queued = eventQueueRef.current.get(runId) ?? [];
    queued.push(event);
    eventQueueRef.current.set(runId, queued);
    if (frameRef.current === null) {
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        flushEvents();
      });
    }
  }, [flushEvents]);

  const loadContext = useCallback(async (runId: string) => {
    contextRunRef.current = runId;
    try {
      const assembly = await alcuinApi.getRunContext(runId);
      if (mountedRef.current && contextRunRef.current === runId) setContextAssembly(assembly);
    } catch {
      if (mountedRef.current && contextRunRef.current === runId) setContextAssembly(null);
    }
  }, []);

  const hydrate = useCallback(async (requestedThreadId: string) => {
    const generation = ++loadGenerationRef.current;
    if (stateRef.current.thread?.id !== requestedThreadId) {
      contextRunRef.current = null;
      setContextAssembly(null);
    }
    dispatch({ type: "hydrate.started", threadId: requestedThreadId });
    try {
      const detail: ThreadDetailShape = await alcuinApi.getThread(requestedThreadId);
      const latestRun = [...detail.runs]
        .sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at))
        .at(-1);
      const latestRunSnapshot = latestRun ? await alcuinApi.getRun(latestRun.id) : null;
      const latestRunEvents = latestRunSnapshot?.events ?? [];
      if (generation !== loadGenerationRef.current) return;
      const hydratedDetail = latestRunSnapshot ? {
        ...detail,
        runs: detail.runs.map((run) => run.id === latestRunSnapshot.id ? latestRunSnapshot : run),
      } : detail;
      dispatch({
        type: "hydrate.completed",
        detail: hydratedDetail,
        latestRunEvents,
        latestRunStatus: latestRunSnapshot?.status,
        latestRunId: latestRunSnapshot?.id,
      });
      if (latestRunSnapshot) void loadContext(latestRunSnapshot.id);
      else {
        contextRunRef.current = null;
        setContextAssembly(null);
      }
      if (latestRunSnapshot?.status === "queued" || latestRunSnapshot?.status === "running") {
        const after = latestRunEvents.reduce((highest, event) => Math.max(highest, event.sequence), 0);
        resumeRunRef.current(latestRunSnapshot, after);
      }
    } catch (error) {
      if (generation !== loadGenerationRef.current) return;
      dispatch({
        type: "hydrate.failed",
        message: error instanceof Error ? error.message : "Unable to load thread",
      });
    }
  }, [loadContext]);

  useEffect(() => {
    if (!threadId) {
      loadGenerationRef.current += 1;
      contextRunRef.current = null;
      setContextAssembly(null);
      dispatch({ type: "reset" });
      return;
    }
    if (stateRef.current.thread?.id === threadId) return;
    void hydrate(threadId);
  }, [hydrate, threadId]);

  useEffect(() => {
    const eventQueue = eventQueueRef.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadGenerationRef.current += 1;
      contextRunRef.current = null;
      eventQueue.clear();
      if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    };
  }, []);

  const ensureThread = useCallback(async (): Promise<Thread> => {
    const existing = stateRef.current.thread;
    if (existing && existing.agent_id === agent?.id) return existing;
    if (!agent) throw new Error("Agent is unavailable");
    const thread = await alcuinApi.createThread(agent.id, hostContext);
    dispatch({ type: "thread.created", thread });
    stateRef.current = { ...stateRef.current, thread };
    onThreadCreated(thread);
    return thread;
  }, [agent, hostContext, onThreadCreated]);

  const finishRun = useCallback(async (run: Run) => {
    flushEvents();
    const snapshot = await alcuinApi.getRun(run.id);
    dispatch({ type: "events.appended", runId: run.id, events: snapshot.events });
    dispatch({ type: "run.settled", runId: run.id, status: snapshot.status });
    await Promise.allSettled([
      loadContext(run.id),
      onRunCreated(run.id),
    ]);
    return snapshot;
  }, [flushEvents, loadContext, onRunCreated]);

  const reconcileRun = useCallback(async (run: Run, message: string) => {
    flushEvents();
    try {
      const snapshot = await alcuinApi.getRun(run.id);
      dispatch({ type: "events.appended", runId: run.id, events: snapshot.events });
      dispatch({ type: "run.settled", runId: run.id, status: snapshot.status });
      await Promise.allSettled([
        loadContext(run.id),
        onRunCreated(run.id),
      ]);
      return snapshot;
    } catch {
      dispatch({ type: "run.connection_failed", runId: run.id, message });
      return null;
    }
  }, [flushEvents, loadContext, onRunCreated]);

  const resumeRun = useCallback((run: Run, after: number) => {
    if (connectedRunRef.current === run.id) return;
    connectedRunRef.current = run.id;
    void (async () => {
      try {
        await alcuinApi.streamRun(run.id, (event) => queueEvent(run.id, event), after);
        await finishRun(run);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Live updates disconnected";
        await reconcileRun(run, message);
      } finally {
        if (connectedRunRef.current === run.id) connectedRunRef.current = null;
      }
    })();
  }, [finishRun, queueEvent, reconcileRun]);

  useEffect(() => { resumeRunRef.current = resumeRun; }, [resumeRun]);

  const send = useCallback(async (
    input: string,
    attachments: AttachmentResource[] = [],
    options: SendOptions = {},
  ) => {
    const value = input.trim();
    if ((!value && attachments.length === 0) || !agent) return;
    if (stateRef.current.phase === "submitting" || stateRef.current.phase === "streaming" || stateRef.current.phase === "waiting_for_approval") return;
    const optimisticId = `optimistic-${crypto.randomUUID()}`;
    let connectedRun: Run | null = null;
    dispatch({ type: "turn.submitted", turn: optimisticTurn(optimisticId, value, attachments) });
    try {
      const thread = await ensureThread();
      const run = await alcuinApi.createRun(thread.id, value, attachments.map((attachment) => attachment.id), options.runOptions ?? {});
      connectedRun = run;
      dispatch({ type: "run.created", optimisticId, run });
      stateRef.current = { ...stateRef.current, activeRunId: run.id, phase: "streaming" };
      connectedRunRef.current = run.id;
      options.onAccepted?.();
      void loadContext(run.id);
      await alcuinApi.streamRun(run.id, (event) => queueEvent(run.id, event));
      await finishRun(run);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Run failed";
      if (connectedRun) await reconcileRun(connectedRun, message);
      else dispatch({ type: "turn.failed", optimisticId, message });
      throw error;
    } finally {
      if (connectedRun && connectedRunRef.current === connectedRun.id) connectedRunRef.current = null;
    }
  }, [agent, ensureThread, finishRun, loadContext, queueEvent, reconcileRun]);

  const runTool = useCallback(async (input: ToolRunInput, options: SendOptions = {}) => {
    if (!agent) return;
    if (stateRef.current.phase === "submitting" || stateRef.current.phase === "streaming" || stateRef.current.phase === "waiting_for_approval") return;
    const optimisticId = `optimistic-${crypto.randomUUID()}`;
    let connectedRun: Run | null = null;
    dispatch({ type: "turn.submitted", turn: optimisticTurn(optimisticId, input.label, []) });
    try {
      const thread = await ensureThread();
      const run = await alcuinApi.createToolRun(
        thread.id,
        input.label,
        input.tool,
        input.arguments,
        input.extensionManifestId,
        input.uiBlockId,
      );
      connectedRun = run;
      dispatch({ type: "run.created", optimisticId, run });
      stateRef.current = { ...stateRef.current, activeRunId: run.id, phase: "streaming" };
      connectedRunRef.current = run.id;
      options.onAccepted?.();
      void loadContext(run.id);
      await alcuinApi.streamRun(run.id, (event) => queueEvent(run.id, event));
      await finishRun(run);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Extension action failed";
      if (connectedRun) await reconcileRun(connectedRun, message);
      else dispatch({ type: "turn.failed", optimisticId, message });
      throw error;
    } finally {
      if (connectedRun && connectedRunRef.current === connectedRun.id) connectedRunRef.current = null;
    }
  }, [agent, ensureThread, finishRun, loadContext, queueEvent, reconcileRun]);

  const refresh = useCallback(async () => {
    flushEvents();
    const currentThreadId = stateRef.current.thread?.id ?? threadId;
    if (currentThreadId) await hydrate(currentThreadId);
  }, [flushEvents, hydrate, threadId]);

  return {
    state,
    contextAssembly,
    ensureThread,
    send,
    runTool,
    refresh,
    running: state.phase === "submitting" || state.phase === "streaming",
    blocked: state.phase === "submitting" || state.phase === "streaming" || state.phase === "waiting_for_approval",
  };
}

export type ThreadSessionController = ReturnType<typeof useThreadSession>;
export type { ThreadSessionState };
