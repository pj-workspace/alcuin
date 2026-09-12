"use client";

import type { Thread } from "@alcuin/contracts";
import { useCallback, useEffect, useRef, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import { threadTitleStatus, threadTitleWatchAction } from "./thread-title-model";

/** Poll only the selected Thread. Reading a sidebar never names all history. */
export function useThreadTitle({ workspaceId, threadId, needsTitle, onUpdated }: {
  workspaceId?: string;
  threadId: string | null;
  needsTitle: boolean;
  onUpdated: (thread: Thread) => void;
}) {
  const [requestVersion, setRequestVersion] = useState(0);
  const scope = useRef({ workspaceId, threadId });
  useEffect(() => { scope.current = { workspaceId, threadId }; }, [workspaceId, threadId]);
  const refreshTitle = useCallback((changedThreadId: string) => {
    if (changedThreadId === scope.current.threadId) setRequestVersion((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!workspaceId || !threadId || !needsTitle) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    let failures = 0;
    let stopped = false;
    let startedAt = performance.now();
    let recovered = false;
    let status: NonNullable<Thread["title_status"]> = "pending";
    const current = () => !controller.signal.aborted && scope.current.workspaceId === workspaceId && scope.current.threadId === threadId;
    const check = async (forceEnsure = false) => {
      if (!current()) return;
      const action = threadTitleWatchAction({ status, attempts, elapsedMs: performance.now() - startedAt, recovered });
      if (action === "stop") { stopped = status !== "ready"; return; }
      const ensure = forceEnsure || action === "ensure";
      if (action === "ensure") recovered = true;
      attempts += 1;
      try {
        const thread = ensure
          ? await alcuinApi.ensureThreadTitle(threadId, controller.signal)
          : await alcuinApi.getThreadTitle(threadId, controller.signal);
        if (!current() || thread.id !== threadId || thread.workspace_id !== workspaceId) return;
        onUpdated(thread);
        failures = 0;
        status = threadTitleStatus(thread);
        // A just-created Thread can be observed before its first input arrives.
        // Activity wakes this watcher again; empty Threads never poll forever.
        if (threadTitleWatchAction({ status, attempts, elapsedMs: performance.now() - startedAt, recovered }) === "stop") { stopped = status !== "ready"; return; }
        timer = setTimeout(() => void check(), Math.min(attempts < 4 ? 650 : 1500, Math.max(0, 55_000 - (performance.now() - startedAt))));
      } catch {
        if (!current()) return;
        failures += 1;
        if (failures >= 3) { stopped = true; return; }
        timer = setTimeout(() => void check(ensure), 1000 * failures);
      }
    };
    // StrictMode replays effects in development. Start after that replay so its
    // discarded setup cannot send a duplicate ensure request to the server.
    timer = setTimeout(() => void check(true), 0);
    const resume = () => {
      if (document.visibilityState === "visible" && stopped && current()) {
        stopped = false; attempts = 0; failures = 0; recovered = false; startedAt = performance.now(); status = "pending";
        void check(true);
      }
    };
    document.addEventListener("visibilitychange", resume);
    return () => { controller.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", resume); };
  }, [workspaceId, threadId, needsTitle, requestVersion, onUpdated]);

  return refreshTitle;
}
