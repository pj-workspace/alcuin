import type { ArtifactResource, Task, TaskEvidence } from "@alcuin/contracts";

/** Task evidence is an invalidation hint, never an artifact body. */
export function taskArtifactRefreshKey(task: (Task & { evidence?: TaskEvidence[] }) | null, threadId: string | null): string | null {
  if (!task || task.thread_id !== threadId) return null;
  const evidenceIds = [...new Set([
    ...(task.evidence ?? []),
    ...task.plan.steps.flatMap((step) => step.evidence),
  ].filter((item) => item.kind === "artifact").map((item) => item.id))].sort();
  const terminal = ["completed", "failed", "cancelled"].includes(task.status) ? task.status : "";
  return JSON.stringify([task.id, task.latest_checkpoint?.id ?? "", terminal, evidenceIds]);
}

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
  baseTitle: string;
  baseContent: string;
  editorEpoch: number;
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
  baseTitle: "",
  baseContent: "",
  editorEpoch: 0,
  conflictLatest: null,
  refreshingConflict: false,
  message: null,
};

export interface ArtifactEditorScope {
  artifactId: string;
  workspaceId: string;
  threadId: string;
  editorEpoch: number;
}

export interface ArtifactDraftValues {
  baseVersion: number;
  baseTitle: string;
  baseContent: string;
  draftTitle: string;
  draftContent: string;
}

export type ArtifactWorkspaceAction =
  | { type: "thread.reset" }
  | { type: "resources.loading" }
  | { type: "resources.loaded"; resources: ArtifactResource[] }
  | { type: "resources.failed"; message: string }
  | { type: "resource.received"; resource: ArtifactResource; select?: boolean }
  | { type: "resource.selected"; id: string }
  | { type: "edit.started" }
  | { type: "draft.restored"; resource: ArtifactResource; draft: ArtifactDraftValues }
  | { type: "draft.changed"; title?: string; content?: string }
  | { type: "edit.cancelled" }
  | { type: "save.started" }
  | { type: "save.succeeded"; resource: ArtifactResource; scope: ArtifactEditorScope }
  | { type: "save.failed"; message: string; scope: ArtifactEditorScope }
  | { type: "save.conflict"; message: string; latest?: ArtifactResource | null; scope: ArtifactEditorScope }
  | { type: "conflict.refreshing" }
  | { type: "conflict.refreshed"; resource: ArtifactResource; scope: ArtifactEditorScope }
  | { type: "conflict.use-latest" }
  | { type: "conflict.keep-mine" }
  | { type: "saved.settled"; scope: ArtifactEditorScope };

export function artifactWorkspaceReducer(
  state: ArtifactWorkspaceState,
  action: ArtifactWorkspaceAction,
): ArtifactWorkspaceState {
  if ("scope" in action && !matchesEditorScope(state, action.scope)) return state;
  if ("scope" in action && "resource" in action && !matchesResourceScope(action.resource, action.scope)) return state;
  if (action.type === "save.conflict" && action.latest && !matchesResourceScope(action.latest, action.scope)) return state;
  if (action.type === "thread.reset") return { ...initialArtifactWorkspaceState, editorEpoch: state.editorEpoch + 1 };
  if (action.type === "resources.loading") return { ...initialArtifactWorkspaceState, editorEpoch: state.editorEpoch + 1, phase: "loading" };
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
    return { ...initialArtifactWorkspaceState, editorEpoch: state.editorEpoch + 1, resources, selectedId, phase: "preview" };
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
      baseTitle: resource.title,
      baseContent: resource.content,
      editorEpoch: state.editorEpoch + 1,
      conflictLatest: null,
      refreshingConflict: false,
      message: null,
    };
  }
  if (action.type === "draft.restored") {
    const selected = selectedArtifact(state);
    if (!selected || selected.id !== action.resource.id || selected.workspace_id !== action.resource.workspace_id
      || selected.thread_id !== action.resource.thread_id || isEditorState(state)) return state;
    const resources = upsertArtifact(state.resources, action.resource);
    const latest = resources.find((item) => item.id === selected.id)!;
    const conflict = latest.version !== action.draft.baseVersion;
    return { ...state, ...action.draft, resources, editorEpoch: state.editorEpoch + 1,
      phase: conflict ? "conflict" : "editing", conflictLatest: conflict ? latest : null,
      refreshingConflict: false, message: conflict ? "Artifact changed while it was being edited" : null };
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
  if (action.type === "save.started") return isEditorState(state) && state.phase !== "conflict" ? { ...state, phase: "saving", message: null } : state;
  if (action.type === "save.succeeded") {
    const resources = upsertArtifact(state.resources, action.resource);
    const latest = resources.find((item) => item.id === action.resource.id)!;
    const superseded = latest.version > action.resource.version;
    return {
      ...state,
      resources,
      selectedId: action.resource.id,
      phase: superseded ? "conflict" : "saved",
      draftTitle: action.resource.title,
      draftContent: action.resource.content,
      baseVersion: action.resource.version,
      baseTitle: action.resource.title,
      baseContent: action.resource.content,
      conflictLatest: superseded ? latest : null,
      refreshingConflict: false,
      message: null,
    };
  }
  if (action.type === "save.failed") return { ...state, phase: "error", message: action.message };
  if (action.type === "save.conflict") {
    const resources = action.latest ? upsertArtifact(state.resources, action.latest) : state.resources;
    const latest = resources.find((item) => item.id === state.selectedId);
    return {
      ...state,
      resources,
      phase: "conflict",
      conflictLatest: latest && latest.version !== state.baseVersion ? latest : state.conflictLatest,
      refreshingConflict: false,
      message: action.message,
    };
  }
  if (action.type === "conflict.refreshing") return state.phase === "conflict" ? { ...state, refreshingConflict: true } : state;
  if (action.type === "conflict.refreshed") {
    if (state.phase !== "conflict") return state;
    const resources = upsertArtifact(state.resources, action.resource);
    return {
      ...state,
      resources,
      conflictLatest: resources.find((item) => item.id === state.selectedId) ?? null,
      refreshingConflict: false,
    };
  }
  if (action.type === "conflict.use-latest" || action.type === "conflict.keep-mine") {
    if (state.phase !== "conflict" || !state.conflictLatest) return state;
    const latest = selectedArtifact(state);
    if (!latest) return state;
    if (action.type === "conflict.use-latest") return editorReset({ ...state, phase: "preview" }, latest);
    return { ...state, baseVersion: latest.version, baseTitle: latest.title, baseContent: latest.content,
      editorEpoch: state.editorEpoch + 1, phase: "editing", conflictLatest: null, refreshingConflict: false, message: null };
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
  return state.baseVersion !== null && (state.draftTitle !== state.baseTitle || state.draftContent !== state.baseContent);
}

export function artifactEditorScope(state: ArtifactWorkspaceState): ArtifactEditorScope | null {
  const resource = selectedArtifact(state);
  return resource ? { artifactId: resource.id, workspaceId: resource.workspace_id,
    threadId: resource.thread_id, editorEpoch: state.editorEpoch } : null;
}

function matchesResourceScope(resource: ArtifactResource, scope: ArtifactEditorScope): boolean {
  return resource.id === scope.artifactId && resource.workspace_id === scope.workspaceId && resource.thread_id === scope.threadId;
}

function matchesEditorScope(state: ArtifactWorkspaceState, scope: ArtifactEditorScope): boolean {
  const resource = selectedArtifact(state);
  return Boolean(resource && matchesResourceScope(resource, scope) && state.editorEpoch === scope.editorEpoch);
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
    baseTitle: resource?.title ?? "",
    baseContent: resource?.content ?? "",
    editorEpoch: state.editorEpoch + 1,
    conflictLatest: null,
    refreshingConflict: false,
    message: null,
  };
}

function isEditorState(state: ArtifactWorkspaceState): boolean {
  return state.baseVersion !== null && ["editing", "saving", "error", "conflict"].includes(state.phase);
}
