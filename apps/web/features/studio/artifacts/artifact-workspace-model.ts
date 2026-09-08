import type { ArtifactResource } from "@alcuin/contracts";

export type ArtifactWorkspacePhase =
  | "idle"
  | "loading"
  | "preview"
  | "editing"
  | "saving"
  | "saved"
  | "conflict"
  | "error";

export interface ArtifactWorkspaceState {
  resources: ArtifactResource[];
  selectedId: string | null;
  pendingSelectedId: string | null;
  phase: ArtifactWorkspacePhase;
  draftTitle: string;
  draftContent: string;
  baseVersion: number | null;
  conflictLatest: ArtifactResource | null;
  refreshingConflict: boolean;
  message: string | null;
}

export const initialArtifactWorkspaceState: ArtifactWorkspaceState = {
  resources: [],
  selectedId: null,
  pendingSelectedId: null,
  phase: "idle",
  draftTitle: "",
  draftContent: "",
  baseVersion: null,
  conflictLatest: null,
  refreshingConflict: false,
  message: null,
};

export type ArtifactWorkspaceAction =
  | { type: "thread.reset" }
  | { type: "resources.loading" }
  | { type: "resources.loaded"; resources: ArtifactResource[] }
  | { type: "resources.failed"; message: string }
  | { type: "resource.received"; resource: ArtifactResource; select?: boolean }
  | { type: "resource.selected"; id: string }
  | { type: "edit.started" }
  | { type: "draft.changed"; title?: string; content?: string }
  | { type: "edit.cancelled" }
  | { type: "save.started" }
  | { type: "save.succeeded"; resource: ArtifactResource }
  | { type: "save.failed"; message: string }
  | { type: "save.conflict"; message: string; latest?: ArtifactResource | null }
  | { type: "conflict.refreshing" }
  | { type: "conflict.refreshed"; resource: ArtifactResource }
  | { type: "saved.settled" };

export function artifactWorkspaceReducer(
  state: ArtifactWorkspaceState,
  action: ArtifactWorkspaceAction,
): ArtifactWorkspaceState {
  if (action.type === "thread.reset") return initialArtifactWorkspaceState;
  if (action.type === "resources.loading") return { ...initialArtifactWorkspaceState, phase: "loading" };
  if (action.type === "resources.loaded") {
    const resources = action.resources.reduce(
      (current, resource) => upsertArtifact(current, resource),
      state.resources,
    );
    const editing = isEditorState(state);
    if (editing || state.phase === "saved") {
      const selected = resources.find((item) => item.id === state.selectedId);
      if (editing && selected && state.baseVersion !== null && selected.version > state.baseVersion) {
        return {
          ...state,
          resources,
          phase: "conflict",
          conflictLatest: selected,
          refreshingConflict: false,
          message: "Artifact changed while it was being edited",
        };
      }
      return { ...state, resources };
    }
    const selectedId = resources.some((item) => item.id === state.selectedId)
      ? state.selectedId
      : resources[0]?.id ?? null;
    return { ...initialArtifactWorkspaceState, resources, selectedId, phase: "preview" };
  }
  if (action.type === "resources.failed") {
    return { ...state, phase: "error", message: action.message };
  }
  if (action.type === "resource.received") {
    const previous = state.resources.find((item) => item.id === action.resource.id);
    const effectiveResource = previous && previous.version > action.resource.version ? previous : action.resource;
    const resources = upsertArtifact(state.resources, effectiveResource);
    const sameOpenArtifact = state.selectedId === effectiveResource.id;
    const editing = isEditorState(state);
    const holdSelection = editing || state.phase === "saved";
    if (sameOpenArtifact && editing && state.baseVersion !== null && effectiveResource.version > state.baseVersion) {
      return {
        ...state,
        resources,
        phase: "conflict",
        conflictLatest: effectiveResource,
        refreshingConflict: false,
        message: "Artifact changed while it was being edited",
      };
    }
    return {
      ...state,
      resources,
      selectedId: !holdSelection && (action.select || !state.selectedId) ? effectiveResource.id : state.selectedId,
      pendingSelectedId: holdSelection && action.select && effectiveResource.id !== state.selectedId
        ? effectiveResource.id
        : state.pendingSelectedId,
      phase: state.phase === "loading" || state.phase === "idle" || !state.selectedId ? "preview" : state.phase,
    };
  }
  if (action.type === "resource.selected") {
    const resource = state.resources.find((item) => item.id === action.id);
    if (!resource) return state;
    return editorReset({ ...state, selectedId: resource.id, pendingSelectedId: null, phase: "preview" }, resource);
  }
  if (action.type === "edit.started") {
    const resource = selectedArtifact(state);
    if (!resource) return state;
    return {
      ...state,
      phase: "editing",
      draftTitle: resource.title,
      draftContent: resource.content,
      baseVersion: resource.version,
      conflictLatest: null,
      refreshingConflict: false,
      message: null,
    };
  }
  if (action.type === "draft.changed") {
    return {
      ...state,
      draftTitle: action.title ?? state.draftTitle,
      draftContent: action.content ?? state.draftContent,
      phase: state.phase === "error" ? "editing" : state.phase,
      message: state.phase === "error" ? null : state.message,
    };
  }
  if (action.type === "edit.cancelled") {
    const pending = state.pendingSelectedId
      ? state.resources.find((item) => item.id === state.pendingSelectedId)
      : undefined;
    return editorReset({
      ...state,
      selectedId: pending?.id ?? state.selectedId,
      pendingSelectedId: null,
      phase: "preview",
    }, pending ?? selectedArtifact(state));
  }
  if (action.type === "save.started") return { ...state, phase: "saving", message: null };
  if (action.type === "save.succeeded") {
    const resources = upsertArtifact(state.resources, action.resource);
    return {
      ...state,
      resources,
      selectedId: action.resource.id,
      phase: "saved",
      draftTitle: action.resource.title,
      draftContent: action.resource.content,
      baseVersion: action.resource.version,
      conflictLatest: null,
      refreshingConflict: false,
      message: null,
    };
  }
  if (action.type === "save.failed") return { ...state, phase: "error", message: action.message };
  if (action.type === "save.conflict") {
    return {
      ...state,
      resources: action.latest ? upsertArtifact(state.resources, action.latest) : state.resources,
      phase: "conflict",
      conflictLatest: action.latest ?? null,
      refreshingConflict: false,
      message: action.message,
    };
  }
  if (action.type === "conflict.refreshing") return { ...state, refreshingConflict: true };
  if (action.type === "conflict.refreshed") {
    return editorReset({
      ...state,
      resources: upsertArtifact(state.resources, action.resource),
      selectedId: action.resource.id,
      pendingSelectedId: null,
      phase: "preview",
      refreshingConflict: false,
    }, action.resource);
  }
  if (action.type === "saved.settled" && state.phase === "saved") {
    const pending = state.pendingSelectedId
      ? state.resources.find((item) => item.id === state.pendingSelectedId)
      : undefined;
    return editorReset({
      ...state,
      selectedId: pending?.id ?? state.selectedId,
      pendingSelectedId: null,
      phase: "preview",
    }, pending ?? selectedArtifact(state));
  }
  return state;
}

export function selectedArtifact(state: ArtifactWorkspaceState): ArtifactResource | undefined {
  return state.resources.find((item) => item.id === state.selectedId);
}

export function artifactDraftHasChanges(state: ArtifactWorkspaceState): boolean {
  const selected = selectedArtifact(state);
  return Boolean(selected && (state.draftTitle !== selected.title || state.draftContent !== selected.content));
}

export function isArtifactResource(value: unknown): value is ArtifactResource {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<ArtifactResource>;
  return typeof item.id === "string"
    && typeof item.workspace_id === "string"
    && typeof item.thread_id === "string"
    && typeof item.source_run_id === "string"
    && typeof item.title === "string"
    && typeof item.kind === "string"
    && ["text/markdown", "text/plain", "text/html", "application/json"].includes(String(item.content_type))
    && typeof item.version === "number"
    && Number.isInteger(item.version)
    && item.version >= 1
    && typeof item.content === "string"
    && typeof item.created_at === "string"
    && typeof item.updated_at === "string";
}

function upsertArtifact(resources: ArtifactResource[], resource: ArtifactResource): ArtifactResource[] {
  const current = resources.find((item) => item.id === resource.id);
  const next = current && current.version > resource.version ? current : resource;
  return sortArtifacts([next, ...resources.filter((item) => item.id !== resource.id)]);
}

function sortArtifacts(resources: ArtifactResource[]): ArtifactResource[] {
  return [...resources].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
}

function editorReset(
  state: ArtifactWorkspaceState,
  resource: ArtifactResource | undefined,
): ArtifactWorkspaceState {
  return {
    ...state,
    draftTitle: resource?.title ?? "",
    draftContent: resource?.content ?? "",
    baseVersion: resource?.version ?? null,
    conflictLatest: null,
    refreshingConflict: false,
    message: null,
  };
}

function isEditorState(state: ArtifactWorkspaceState): boolean {
  return state.baseVersion !== null && ["editing", "saving", "error", "conflict"].includes(state.phase);
}
