"use client";

import type { Agent, ArtifactReferenceMessagePart, AttachmentResource, ContextAssembly, ExecutionEvent, Run, Thread } from "@alcuin/contracts";
import type { CreateRunOptions } from "@alcuin/sdk";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import { stopRecoveryDelay } from "@/features/studio/stop-recovery";
import { boundedRunSnapshot, isCurrentRunScope } from "@/features/studio/run-snapshot-guard";
import {
  initialThreadSessionState,
  isCurrentSubmissionScope,
  optimisticTurn,
  threadSessionReducer,
  type ThreadDetailShape,
  type ThreadSessionState,
} from "@/features/studio/thread-session-reducer";

type SendOptions = {
  onAccepted?: () => void;
  runOptions?: CreateRunOptions;
  artifactReferences?: ArtifactReferenceMessagePart[];
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
  const [stopRecoveryRunId, setStopRecoveryRunId] = useState<string | null>(null);
  const stateRef = useRef(state);
  const loadGenerationRef = useRef(0);
  const connectedRunRef = useRef<string | null>(null);
  const contextRunRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const scopeRef = useRef({ agentId: agent?.id, threadId });
  const pendingThreadRef = useRef<{ scope: typeof scopeRef.current; promise: Promise<Thread> } | null>(null);
  const resumeRunRef = useRef<(run: Run, after: number) => void>(() => undefined);
  const eventQueueRef = useRef(new Map<string, ExecutionEvent[]>());
  const frameRef = useRef<number | null>(null);
  const stoppingRunRef = useRef<string | null>(null);
  const stopRequestRef = useRef<{ controller: AbortController; timer: ReturnType<typeof setTimeout> } | null>(null);

  useEffect(() => () => {
    const request = stopRequestRef.current;
    if (request) { clearTimeout(request.timer); request.controller.abort(); }
    stopRequestRef.current = null;
    stoppingRunRef.current = null;
  }, [agent?.id, threadId]);

  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => {
    const previous = scopeRef.current;
    // The navigation caused by our own newly created Thread keeps its submission.
    if (previous.agentId === agent?.id && previous.threadId === null && threadId
      && stateRef.current.thread?.id === threadId && stateRef.current.thread.agent_id === agent?.id) {
      previous.threadId = threadId;
    } else scopeRef.current = { agentId: agent?.id, threadId };
  }, [agent?.id, threadId]);

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
      if (latestRunSnapshot?.status === "queued" || latestRunSnapshot?.status === "running" || latestRunSnapshot?.status === "cancel_requested") {
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
      stateRef.current = initialThreadSessionState;
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
    const scope = scopeRef.current;
    const existing = stateRef.current.thread;
    if (existing && existing.agent_id === agent?.id && (!scope.threadId || scope.threadId === existing.id)) return existing;
    if (!agent) throw new Error("Agent is unavailable");
    if (scope.threadId) throw new Error("Wait for the conversation to finish loading");
    if (pendingThreadRef.current?.scope === scope) return pendingThreadRef.current.promise;
    const promise = (async () => {
      const thread = await alcuinApi.createThread(agent.id, hostContext);
      if (!mountedRef.current || scopeRef.current !== scope) throw new DOMException("Conversation changed", "AbortError");
      dispatch({ type: "thread.created", thread });
      stateRef.current = { ...stateRef.current, thread };
      onThreadCreated(thread);
      return thread;
    })();
    pendingThreadRef.current = { scope, promise };
    try { return await promise; }
    finally { if (pendingThreadRef.current?.promise === promise) pendingThreadRef.current = null; }
  }, [agent, hostContext, onThreadCreated]);

  const isCurrentRun = useCallback((run: Run) => isCurrentRunScope({
    mounted: mountedRef.current,
    loading: stateRef.current.phase === "loading",
    threadId: stateRef.current.thread?.id ?? null,
    activeRunId: stateRef.current.activeRunId,
    latestRunId: stateRef.current.turns.at(-1)?.runId ?? null,
  }, run) && stateRef.current.thread?.agent_id === scopeRef.current.agentId
    && (!scopeRef.current.threadId || scopeRef.current.threadId === run.thread_id), []);

  const finishRun = useCallback(async (run: Run) => {
    if (!isCurrentRun(run)) return null;
    flushEvents();
    const snapshot = await boundedRunSnapshot((signal) => alcuinApi.getRun(run.id, signal));
    if (!isCurrentRun(run)) return null;
    dispatch({ type: "events.appended", runId: run.id, events: snapshot.events });
    dispatch({ type: "run.settled", runId: run.id, status: snapshot.status });
    if (snapshot.status === "cancel_requested" && stateRef.current.activeRunId === run.id) setStopRecoveryRunId(run.id);
    await Promise.allSettled([
      loadContext(run.id),
      onRunCreated(run.id),
    ]);
    return snapshot;
  }, [flushEvents, isCurrentRun, loadContext, onRunCreated]);

  const reconcileRun = useCallback(async (run: Run, message: string) => {
    if (!isCurrentRun(run)) return null;
    flushEvents();
    try {
      const snapshot = await boundedRunSnapshot((signal) => alcuinApi.getRun(run.id, signal));
      if (!isCurrentRun(run)) return null;
      dispatch({ type: "events.appended", runId: run.id, events: snapshot.events });
      dispatch({ type: "run.settled", runId: run.id, status: snapshot.status });
      if (snapshot.status === "cancel_requested" && stateRef.current.activeRunId === run.id) setStopRecoveryRunId(run.id);
      await Promise.allSettled([
        loadContext(run.id),
        onRunCreated(run.id),
      ]);
      return snapshot;
    } catch {
      if (!isCurrentRun(run)) return null;
      dispatch({ type: "run.connection_failed", runId: run.id, message });
      if (stateRef.current.turns.some((turn) => turn.runId === run.id && turn.status === "cancel_requested")) setStopRecoveryRunId(run.id);
      return null;
    }
  }, [flushEvents, isCurrentRun, loadContext, onRunCreated]);

  useEffect(() => {
    const runId = stopRecoveryRunId;
    if (!runId || state.activeRunId !== runId) return;
    const scope = scopeRef.current;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let requestTimer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let attempt = 0;
    const current = () => !disposed && mountedRef.current && scopeRef.current === scope
      && stateRef.current.activeRunId === runId;
    const schedule = () => {
      if (!current()) return;
      const delay = stopRecoveryDelay(attempt++);
      if (delay === null) {
        dispatch({ type: "stop.disconnected", runId });
        setStopRecoveryRunId(null);
        return;
      }
      timer = setTimeout(async () => {
        controller = new AbortController();
        requestTimer = setTimeout(() => controller?.abort(), 5_000);
        try {
          const snapshot = await alcuinApi.getRun(runId, controller.signal);
          if (!current()) return;
          flushEvents();
          dispatch({ type: "events.appended", runId, events: snapshot.events });
          dispatch({ type: "run.settled", runId, status: snapshot.status });
          if (["cancelled", "completed", "failed"].includes(snapshot.status)) {
            setStopRecoveryRunId(null);
            void Promise.allSettled([onRunCreated(runId)]);
            return;
          }
        } catch { /* Keep the accepted stop visible until bounded recovery ends. */ }
        finally { clearTimeout(requestTimer); }
        schedule();
      }, delay);
    };
    schedule();
    return () => { disposed = true; clearTimeout(timer); clearTimeout(requestTimer); controller?.abort(); };
  }, [stopRecoveryRunId, state.activeRunId, threadId, agent?.id, flushEvents, onRunCreated]);

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
    if (["submitting", "streaming", "waiting_for_approval", "waiting_for_input"].includes(stateRef.current.phase)) return;
    const optimisticId = `optimistic-${crypto.randomUUID()}`;
    let connectedRun: Run | null = null;
    const references = options.artifactReferences ?? [];
    if (references.length && !value) return;
    const scope = scopeRef.current;
    let targetThreadId: string | null = null;
    const current = () => isCurrentSubmissionScope(scope, scopeRef.current, mountedRef.current, targetThreadId);
    dispatch({ type: "turn.submitted", turn: optimisticTurn(optimisticId, value, attachments, references) });
    try {
      const thread = await ensureThread();
      targetThreadId = thread.id;
      if (!current()) return;
      const run = await alcuinApi.createRun(thread.id, value, attachments.map((attachment) => attachment.id), {
        ...options.runOptions,
        ...(references.length ? { artifactReferences: references.map((reference) => ({ artifact_id: reference.artifact_id, expected_version: reference.version })) } : {}),
      });
      connectedRun = run;
      if (!current()) return;
      dispatch({ type: "run.created", optimisticId, run });
      stateRef.current = { ...stateRef.current, activeRunId: run.id, phase: "streaming" };
      connectedRunRef.current = run.id;
      options.onAccepted?.();
      if (references.length && run.input_message_id) {
        void boundedRunSnapshot(async (signal) => {
          let after = 0;
          while (current() && !signal.aborted) {
            const messages = await alcuinApi.listThreadMessages(run.thread_id, { after, limit: 500, signal });
            if (!current() || signal.aborted) return;
            const message = messages.find((item) => item.id === run.input_message_id
              && item.run_id === run.id && item.thread_id === run.thread_id && item.role === "user");
            if (message) {
              dispatch({ type: "references.hydrated", threadId: run.thread_id, runId: run.id, message });
              return;
            }
            if (messages.length < 500) return;
            const next = Math.max(...messages.map((item) => item.sequence));
            if (next <= after) return;
            after = next;
          }
        }).catch(() => undefined);
      }
      void loadContext(run.id);
      await alcuinApi.streamRun(run.id, (event) => { if (current()) queueEvent(run.id, event); });
      if (!current()) return;
      await finishRun(run);
    } catch (error) {
      if (!current()) return;
      const message = error instanceof Error ? error.message : "Run failed";
      if (connectedRun) await reconcileRun(connectedRun, message);
      else dispatch({ type: "turn.failed", optimisticId, message });
      if (!current()) return;
      throw error;
    } finally {
      if (connectedRun && connectedRunRef.current === connectedRun.id) connectedRunRef.current = null;
    }
  }, [agent, ensureThread, finishRun, loadContext, queueEvent, reconcileRun]);

  const runTool = useCallback(async (input: ToolRunInput, options: SendOptions = {}) => {
    if (!agent) return;
    if (["submitting", "streaming", "waiting_for_approval", "waiting_for_input"].includes(stateRef.current.phase)) return;
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

  const stop = useCallback(async () => {
    const runId = stateRef.current.activeRunId;
    if (!runId || stoppingRunRef.current === runId) return;
    const scope = scopeRef.current;
    const isCurrent = () => mountedRef.current && scopeRef.current === scope && stateRef.current.activeRunId === runId;
    stoppingRunRef.current = runId;
    setStopRecoveryRunId(null);
    dispatch({ type: "stop.requested", runId });
    const boundedRequest = async <T,>(request: (signal: AbortSignal) => Promise<T>): Promise<T> => {
      const controller = new AbortController();
      const entry = { controller, timer: setTimeout(() => controller.abort(), 5_000) };
      stopRequestRef.current = entry;
      try { return await request(controller.signal); }
      finally {
        clearTimeout(entry.timer);
        if (stopRequestRef.current === entry) stopRequestRef.current = null;
      }
    };
    try {
      let snapshot;
      try { snapshot = await boundedRequest((signal) => alcuinApi.cancelRun(runId, signal)); }
      catch {
        if (!isCurrent()) return;
        snapshot = await boundedRequest((signal) => alcuinApi.getRun(runId, signal));
      }
      if (!isCurrent()) return;
      flushEvents();
      dispatch({ type: "events.appended", runId, events: snapshot.events });
      dispatch({ type: "run.settled", runId, status: snapshot.status });
      if (!["cancel_requested", "cancelled", "completed", "failed"].includes(snapshot.status)) {
        dispatch({ type: "stop.failed", runId });
      } else if (snapshot.status === "cancel_requested") {
        // Waiting runs have no open stream; reconnect for the durable receipt.
        resumeRun(snapshot, snapshot.events.reduce((highest, event) => Math.max(highest, event.sequence), 0));
      }
    } catch {
      if (isCurrent()) dispatch({ type: stateRef.current.turns.some((turn) => turn.runId === runId && turn.status === "cancel_requested") ? "stop.disconnected" : "stop.failed", runId });
    } finally {
      if (stoppingRunRef.current === runId) stoppingRunRef.current = null;
    }
  }, [flushEvents, resumeRun]);

  return {
    state,
    contextAssembly,
    ensureThread,
    send,
    runTool,
    refresh,
    stop,
    running: state.phase === "submitting" || state.phase === "streaming",
    blocked: ["submitting", "streaming", "waiting_for_approval", "waiting_for_input"].includes(state.phase),
  };
}

export type ThreadSessionController = ReturnType<typeof useThreadSession>;
export type { ThreadSessionState };
