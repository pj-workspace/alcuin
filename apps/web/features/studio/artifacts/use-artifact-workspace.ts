"use client";

import { AlcuinApiError } from "@alcuin/sdk";
import type { ArtifactResource } from "@alcuin/contracts";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import {
  artifactDraftHasChanges,
  artifactWorkspaceReducer,
  initialArtifactWorkspaceState,
  isArtifactResource,
  selectedArtifact,
} from "./artifact-workspace-model";

export function useArtifactWorkspace({
  threadId,
  eventArtifacts,
  running,
  refreshKey,
}: {
  threadId: string | null;
  eventArtifacts: readonly unknown[];
  running: boolean;
  refreshKey?: string | null;
}) {
  const [state, dispatch] = useReducer(artifactWorkspaceReducer, initialArtifactWorkspaceState);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const generationRef = useRef(0);
  const loadedThreadRef = useRef<string | null>(null);
  const latestEventIdRef = useRef<string | null>(null);
  const receivedEventsRef = useRef(new Map<string, ArtifactResource>());

  const load = useCallback(async (requestedThreadId: string, background = false) => {
    const generation = ++generationRef.current;
    setRefreshError(null);
    if (!background) dispatch({ type: "resources.loading" });
    try {
      const resources = await alcuinApi.listArtifacts(requestedThreadId);
      if (generation === generationRef.current) dispatch({ type: "resources.loaded", resources });
    } catch (error) {
      if (generation !== generationRef.current) return;
      const message = error instanceof Error ? error.message : "Unable to load artifacts";
      if (background) setRefreshError(message);
      else dispatch({ type: "resources.failed", message });
    }
  }, []);

  useEffect(() => {
    latestEventIdRef.current = null;
    receivedEventsRef.current.clear();
    if (!threadId) {
      generationRef.current += 1;
      dispatch({ type: "thread.reset" });
      return;
    }
  }, [threadId]);

  useEffect(() => {
    const background = loadedThreadRef.current === threadId;
    loadedThreadRef.current = threadId;
    if (threadId) void load(threadId, background);
    return () => { generationRef.current += 1; };
  }, [load, threadId, refreshKey]);

  useEffect(() => {
    const resources = eventArtifacts.filter(isArtifactResource).filter((item) => item.thread_id === threadId);
    const latest = resources.at(-1);
    for (const resource of resources) {
      if (receivedEventsRef.current.get(resource.id) === resource) continue;
      receivedEventsRef.current.set(resource.id, resource);
      dispatch({ type: "resource.received", resource, select: resource.id === latest?.id && resource.id !== latestEventIdRef.current });
    }
    if (latest) latestEventIdRef.current = latest.id;
  }, [eventArtifacts, threadId]);

  useEffect(() => {
    if (state.phase !== "saved") return;
    const timeout = window.setTimeout(() => dispatch({ type: "saved.settled" }), 1_000);
    return () => window.clearTimeout(timeout);
  }, [state.phase]);

  const selected = selectedArtifact(state);
  const editorOpen = Boolean(selected && state.baseVersion !== null && ["editing", "saving", "error", "conflict"].includes(state.phase));
  const displayedArtifact = selected;

  const save = useCallback(async () => {
    const resource = selectedArtifact(state);
    if (!resource || state.baseVersion === null || state.phase === "saving" || !state.draftTitle.trim()) return;
    dispatch({ type: "save.started" });
    try {
      const updated = await alcuinApi.updateArtifact(resource.id, {
        expected_version: state.baseVersion,
        title: state.draftTitle.trim(),
        content: state.draftContent,
      });
      dispatch({ type: "save.succeeded", resource: updated });
    } catch (error) {
      const conflict = error instanceof AlcuinApiError
        && error.status === 409
        && typeof error.detail === "object"
        && error.detail !== null
        && (error.detail as { code?: string }).code === "artifact_version_conflict";
      if (conflict) {
        const latest = await alcuinApi.getArtifact(resource.id).catch(() => null);
        dispatch({ type: "save.conflict", message: error.message, latest });
        return;
      }
      dispatch({ type: "save.failed", message: error instanceof Error ? error.message : "Unable to save artifact" });
    }
  }, [state]);

  const refreshConflict = useCallback(async () => {
    const resource = selectedArtifact(state);
    if (!resource || state.refreshingConflict) return;
    dispatch({ type: "conflict.refreshing" });
    try {
      const latest = await alcuinApi.getArtifact(resource.id);
      dispatch({ type: "conflict.refreshed", resource: latest });
    } catch (error) {
      dispatch({ type: "save.conflict", message: error instanceof Error ? error.message : "Unable to refresh artifact" });
    }
  }, [state]);

  return {
    state,
    refreshError,
    displayedArtifact,
    selected,
    editorOpen,
    canEdit: Boolean(selected && displayedArtifact?.id === selected.id && !running && state.phase !== "loading"),
    canSave: artifactDraftHasChanges(state) && Boolean(state.draftTitle.trim()) && state.phase !== "saving" && state.phase !== "conflict",
    reload: () => { if (threadId) void load(threadId, true); },
    select: (id: string) => dispatch({ type: "resource.selected", id }),
    beginEdit: () => dispatch({ type: "edit.started" }),
    changeTitle: (title: string) => dispatch({ type: "draft.changed", title }),
    changeContent: (content: string) => dispatch({ type: "draft.changed", content }),
    cancel: () => dispatch({ type: "edit.cancelled" }),
    save,
    refreshConflict,
  };
}

export type ArtifactWorkspaceController = ReturnType<typeof useArtifactWorkspace>;
