"use client";

import type { TaskPlanProposalRequest, Thread } from "@alcuin/contracts";
import { useCallback, useEffect, useRef, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import type { TaskPlanDraft } from "./task-session-reducer";

type ProposalState = {
  scope: { agentId?: string; threadId: string | null } | null;
  phase: "idle" | "generating" | "review" | "error";
  draft: TaskPlanDraft | null;
  goal: string;
  model: string | null;
  error: string | null;
  profile: Omit<TaskPlanProposalRequest, "goal">;
};

const emptyState: ProposalState = { scope: null, phase: "idle", draft: null, goal: "", model: null, error: null, profile: {} };

/** Proposals are ephemeral and scoped to the originating Agent and Thread. */
export function useTaskProposal({ agentId, threadId, ensureThread }: {
  agentId?: string;
  threadId: string | null;
  ensureThread: () => Promise<Thread>;
}) {
  const [state, setState] = useState<ProposalState>(emptyState);
  const scopeRef = useRef({ agentId, threadId });
  const requestRef = useRef<{ controller: AbortController; agentId?: string; threadId: string | null; creating: boolean } | null>(null);
  const mountedRef = useRef(true);
  const originRef = useRef<{ agentId?: string; threadId: string | null } | null>(null);

  const cancel = useCallback(() => {
    requestRef.current?.controller.abort();
    requestRef.current = null;
    originRef.current = null;
    setState(emptyState);
  }, []);

  useEffect(() => {
    scopeRef.current = { agentId, threadId };
    const request = requestRef.current;
    const origin = originRef.current;
    if (request && (request.agentId !== agentId || (request.threadId !== threadId && !(request.creating && (threadId === null || request.threadId === null))))) cancel();
    else if (origin && (origin.agentId !== agentId || origin.threadId !== threadId)) cancel();
    if (request?.creating && request.threadId === threadId && threadId !== null) request.creating = false;
  }, [agentId, threadId, cancel]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; requestRef.current?.controller.abort(); };
  }, []);

  const generate = useCallback(async (goal: string, profile: Omit<TaskPlanProposalRequest, "goal"> = {}) => {
    const value = goal.trim();
    if (!value || !scopeRef.current.agentId) return;
    requestRef.current?.controller.abort();
    const request = { controller: new AbortController(), ...scopeRef.current, creating: !scopeRef.current.threadId };
    requestRef.current = request;
    originRef.current = null;
    setState({ ...emptyState, scope: { ...scopeRef.current }, phase: "generating", goal: value, profile });
    const isCurrent = () => mountedRef.current && requestRef.current === request && !request.controller.signal.aborted
      && scopeRef.current.agentId === request.agentId
      && (scopeRef.current.threadId === request.threadId || (request.creating && scopeRef.current.threadId === null));
    try {
      const thread = await ensureThread();
      const currentThreadId = scopeRef.current.threadId;
      if (request.controller.signal.aborted || requestRef.current !== request || !mountedRef.current
        || scopeRef.current.agentId !== request.agentId
        || (currentThreadId !== null && currentThreadId !== thread.id)) return;
      request.threadId = thread.id;
      if (currentThreadId === thread.id) request.creating = false;
      setState((current) => ({ ...current, scope: { agentId: request.agentId, threadId: thread.id } }));
      const result = await alcuinApi.proposeTaskPlan(thread.id, { goal: value, ...profile }, request.controller.signal);
      if (!isCurrent()) return;
      originRef.current = { agentId: request.agentId, threadId: thread.id };
      setState({
        scope: { agentId: request.agentId, threadId: thread.id },
        phase: "review", goal: result.goal, model: result.model, error: null,
        profile: { model_override: result.model, reasoning_effort: result.reasoning_effort },
        draft: { goal: result.goal, steps: result.steps.map((step, ordinal) => ({ ...step, id: `proposal:${ordinal}`, ordinal })) },
      });
    } catch (error) {
      if (!isCurrent()) return;
      originRef.current = { agentId: request.agentId, threadId: request.threadId };
      setState({ ...emptyState, scope: { agentId: request.agentId, threadId: request.threadId }, phase: "error", goal: value, profile, error: error instanceof Error ? error.message : "Planning unavailable" });
    } finally {
      if (requestRef.current === request) {
        if (!isCurrent()) cancel();
        requestRef.current = null;
      }
    }
  }, [cancel, ensureThread]);

  const writeManually = useCallback(() => setState((current) => ({
    ...current, phase: "review", error: null, model: null,
    draft: { goal: current.goal, steps: [{ id: "manual:0", ordinal: 0, title: "", description: "" }] },
  })), []);

  return {
    ...state, generate, cancel, writeManually,
    changeDraft: (draft: TaskPlanDraft) => setState((current) => ({ ...current, draft })),
  };
}

export type TaskProposalController = ReturnType<typeof useTaskProposal>;
