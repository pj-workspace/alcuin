"use client";

import { AlcuinApiError } from "@alcuin/sdk";
import type { ArtifactResource } from "@alcuin/contracts";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { alcuinApi } from "@/shared/lib/api";
import { artifactDraftStore } from "./artifact-draft-store";
import {
  artifactEditorScope,
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
  const [draftRevision, touchDraft] = useReducer((value: number) => value + 1, 0);
  const [storageWarning, setStorageWarning] = useState(false);
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
    const scope = artifactEditorScope(state);
    if (!scope) return;
    const timeout = window.setTimeout(() => dispatch({ type: "saved.settled", scope }), 1_000);
    return () => window.clearTimeout(timeout);
  }, [state]);

  useEffect(() => {
    const flush = () => { setStorageWarning(artifactDraftStore.flushAll().some((item) => item.status === "memory-only")); };
    const timer = window.setTimeout(flush, 250);
    return () => window.clearTimeout(timer);
  }, [draftRevision]);
  useEffect(() => {
    const flush = () => { artifactDraftStore.flushAll(); };
    const warn = (event: BeforeUnloadEvent) => {
      if (artifactDraftStore.flushAll().some((item) => item.draft || item.status === "memory-only")) {
        event.preventDefault(); event.returnValue = "";
      }
    };
    window.addEventListener("pagehide", flush);
    window.addEventListener("beforeunload", warn);
    return () => { flush(); window.removeEventListener("pagehide", flush); window.removeEventListener("beforeunload", warn); };
  }, []);

  const selected = selectedArtifact(state);
  const editorOpen = Boolean(selected && state.baseVersion !== null && ["editing", "saving", "error", "conflict"].includes(state.phase));
  const displayedArtifact = selected;
  const scope = draftScope(selected);
  const recovery = scope ? artifactDraftStore.read(scope).draft : null;
  const persist = (title = state.draftTitle, content = state.draftContent, base = state) => {
    if (!scope || base.baseVersion === null) return;
    const previous = artifactDraftStore.read(scope).draft;
    if (title === base.baseTitle && content === base.baseContent) {
      if (previous) artifactDraftStore.remove(scope, previous.token);
    } else {
      artifactDraftStore.write(scope, { baseVersion: base.baseVersion, baseTitle: base.baseTitle,
        baseContent: base.baseContent, title, content });
    }
    touchDraft();
  };
  const discard = () => {
    if (scope && recovery) {
      const result = artifactDraftStore.remove(scope, recovery.token);
      setStorageWarning(result.status === "memory-only");
    }
    touchDraft();
    dispatch({ type: "edit.cancelled" });
  };

  const save = useCallback(async () => {
    const resource = selectedArtifact(state);
    const scope = artifactEditorScope(state);
    if (!resource || !scope || state.baseVersion === null || state.phase === "saving" || state.phase === "conflict" || !state.draftTitle.trim()) return;
    const storageScope = draftScope(resource)!;
    const token = artifactDraftStore.read(storageScope).draft?.token;
    dispatch({ type: "save.started" });
    try {
      const updated = await alcuinApi.updateArtifact(resource.id, {
        expected_version: state.baseVersion,
        title: state.draftTitle.trim(),
        content: state.draftContent,
      });
      if (token) setStorageWarning(artifactDraftStore.remove(storageScope, token).status === "memory-only");
      dispatch({ type: "save.succeeded", resource: updated, scope });
    } catch (error) {
      const conflict = error instanceof AlcuinApiError
        && error.status === 409
        && typeof error.detail === "object"
        && error.detail !== null
        && (error.detail as { code?: string }).code === "artifact_version_conflict";
      if (conflict) {
        const latest = await alcuinApi.getArtifact(resource.id).catch(() => null);
        dispatch({ type: "save.conflict", message: error.message, latest, scope });
        return;
      }
      dispatch({ type: "save.failed", message: error instanceof Error ? error.message : "Unable to save artifact", scope });
    }
  }, [state]);

  const refreshConflict = useCallback(async () => {
    const resource = selectedArtifact(state);
    const scope = artifactEditorScope(state);
    if (!resource || !scope || state.refreshingConflict) return;
    dispatch({ type: "conflict.refreshing" });
    try {
      const latest = await alcuinApi.getArtifact(resource.id);
      dispatch({ type: "conflict.refreshed", resource: latest, scope });
    } catch (error) {
      dispatch({ type: "save.conflict", message: error instanceof Error ? error.message : "Unable to refresh artifact", scope });
    }
  }, [state]);

  return {
    state,
    refreshError,
    displayedArtifact,
    selected,
    editorOpen,
    recovery,
    storageWarning,
    discard,
    hasDraft: (resource: ArtifactResource) => Boolean(artifactDraftStore.read({ workspaceId: resource.workspace_id, threadId: resource.thread_id, artifactId: resource.id }).draft),
    keepMine: () => {
      const latest = state.conflictLatest;
      if (!latest) return;
      persist(state.draftTitle, state.draftContent, { ...state, baseVersion: latest.version, baseTitle: latest.title, baseContent: latest.content });
      dispatch({ type: "conflict.keep-mine" });
    },
    canEdit: Boolean(selected && displayedArtifact?.id === selected.id && !running && state.phase !== "loading"),
    canSave: artifactDraftHasChanges(state) && Boolean(state.draftTitle.trim()) && state.phase !== "saving" && state.phase !== "conflict",
    reload: () => { if (threadId) void load(threadId, true); },
    select: (id: string) => dispatch({ type: "resource.selected", id }),
    beginEdit: () => {
      if (recovery && selected) dispatch({ type: "draft.restored", resource: selected, draft: {
        baseVersion: recovery.baseVersion, baseTitle: recovery.baseTitle, baseContent: recovery.baseContent,
        draftTitle: recovery.title, draftContent: recovery.content,
      } });
      else dispatch({ type: "edit.started" });
    },
    changeTitle: (title: string) => { persist(title); dispatch({ type: "draft.changed", title }); },
    changeContent: (content: string) => { persist(state.draftTitle, content); dispatch({ type: "draft.changed", content }); },
    cancel: () => dispatch({ type: "edit.cancelled" }),
    save,
    refreshConflict,
  };
}

export type ArtifactWorkspaceController = ReturnType<typeof useArtifactWorkspace>;

function draftScope(resource: ArtifactResource | undefined) {
  return resource ? { workspaceId: resource.workspace_id, threadId: resource.thread_id, artifactId: resource.id } : null;
}
