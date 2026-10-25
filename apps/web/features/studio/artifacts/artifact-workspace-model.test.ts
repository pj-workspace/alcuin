import assert from "node:assert/strict";
import test from "node:test";

import type { ArtifactResource, Task } from "@alcuin/contracts";

import {
  artifactDraftHasChanges,
  artifactEditorScope,
  artifactWorkspaceReducer,
  initialArtifactWorkspaceState,
  selectedArtifact,
  taskArtifactRefreshKey,
} from "./artifact-workspace-model.ts";
import { resolveDisplayedArtifact } from "./live-artifact-model.ts";
import { ARTIFACT_SANDBOX, artifactDownloadName, artifactPreviewPolicy } from "./artifact-html.ts";
import { citationsForArtifact } from "./artifact-citation-model.ts";
import { artifactDocumentBody } from "./artifact-document-model.ts";

function resource(version: number, content = `Saved ${version}`, id = "art_1"): ArtifactResource {
  return {
    id,
    workspace_id: "ws_1",
    thread_id: "thr_1",
    source_run_id: `run_${id}`,
    title: id === "art_1" ? "Risk brief" : "New run brief",
    kind: "document",
    content_type: "text/markdown",
    version,
    content,
    created_at: "2026-08-29T00:00:00Z",
    updated_at: `2026-08-29T00:0${version}:00Z`,
  };
}

test("only explicit Artifact resources project to the canvas, never ordinary replies", () => {
  assert.equal(resolveDisplayedArtifact({ enabled: true }), undefined);
  assert.equal(resolveDisplayedArtifact({ enabled: true, eventArtifact: { content: "An ordinary chat reply" } }), undefined);
  assert.equal(resolveDisplayedArtifact({ enabled: false, eventArtifact: resource(1) }), undefined);
  const html = { ...resource(1), content_type: "text/html" as const, content: "<button>Run</button>" };
  assert.deepEqual(resolveDisplayedArtifact({ enabled: true, eventArtifact: html }), html);
});

test("HTML preview uses an opaque origin and scripts are disabled during generation", () => {
  assert.equal(ARTIFACT_SANDBOX, "allow-scripts");
  assert.doesNotMatch(ARTIFACT_SANDBOX, /allow-same-origin|allow-top-navigation|allow-popups|allow-forms/);
  const generating = artifactPreviewPolicy(false);
  assert.match(generating, /script-src 'none'/);
  const interactive = artifactPreviewPolicy(true);
  assert.match(interactive, /script-src 'unsafe-inline'/);
  assert.match(interactive, /connect-src 'none'/);
  assert.match(interactive, /frame-src 'none'/);
  assert.match(interactive, /form-action 'none'/);
  assert.doesNotMatch(interactive, /https?:|unsafe-eval/);
});

test("download filenames preserve multilingual titles without path control characters", () => {
  assert.equal(artifactDownloadName("风险简报", "docx"), "风险简报.docx");
  assert.equal(artifactDownloadName("../Demo:<test>\u0000", "html"), "-Demo--test--.html");
  assert.equal(artifactDownloadName(" ... ", "md"), "Alcuin artifact.md");
});

test("Artifact citations only use its source Run even when citation ids repeat", () => {
  const current = { id: "evt_source", sequence: 1, run_id: "run_art_1", type: "citation.created", timestamp: "2026-09-08T00:00:00Z", payload: { citation_id: "s1", label: "Actual source" } };
  const unrelated = { ...current, id: "evt_other", run_id: "run_other", payload: { citation_id: "s1", label: "Wrong run source" } };
  const nonCitation = { ...current, id: "evt_delta", type: "message.delta", payload: { delta: "Reply" } };
  assert.deepEqual(citationsForArtifact(resource(1), [unrelated, current, nonCitation]), [current]);
  assert.deepEqual(citationsForArtifact({ source_run_id: "run_history" }, [unrelated, current]), []);
});

test("Canvas removes only a matching opening H1 while retaining all following content", () => {
  assert.equal(artifactDocumentBody("# 番茄钟使用说明\n\n正文\n\n# 番茄钟使用说明", "番茄钟使用说明"), "正文\n\n# 番茄钟使用说明");
  assert.equal(artifactDocumentBody("\n  # Capacity brief ###\r\n\r\nFindings", "Capacity brief"), "Findings");
  assert.equal(artifactDocumentBody("Capacity brief\n===\n\nFindings", "Capacity brief"), "Findings");
  for (const content of ["# Another title\n\n# Capacity brief", "## Capacity brief\n\nFindings", "Intro\n\n# Capacity brief", "```md\n# Capacity brief\n```", "# Capacity briefing\n\nFindings"]) {
    assert.equal(artifactDocumentBody(content, "Capacity brief"), content);
  }
});

test("canonical list data wins over an older run event snapshot", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loading" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(1), select: true });
  state = artifactWorkspaceReducer(state, { type: "resources.loaded", resources: [resource(3)] });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(1), select: true });
  assert.equal(selectedArtifact(state)?.version, 3);
  assert.equal(selectedArtifact(state)?.content, "Saved 3");
});

test("supports edit, save success, and cancel without exposing version workflows", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Local edit" });
  assert.equal(artifactDraftHasChanges(state), true);
  state = artifactWorkspaceReducer(state, { type: "save.started" });
  state = artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(3, "Local edit"), scope: artifactEditorScope(state)! });
  assert.equal(state.phase, "saved");
  assert.equal(selectedArtifact(state)?.content, "Local edit");
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", title: "Unsaved" });
  state = artifactWorkspaceReducer(state, { type: "edit.cancelled" });
  assert.equal(state.phase, "preview");
  assert.equal(state.draftTitle, "Risk brief");
});

test("refreshing a conflict preserves the draft until explicitly choosing latest", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "My unsaved draft" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(3, "Remote edit") });
  assert.equal(state.phase, "conflict");
  assert.equal(state.draftContent, "My unsaved draft");
  state = artifactWorkspaceReducer(state, { type: "conflict.refreshed", resource: resource(4, "Latest remote edit"), scope: artifactEditorScope(state)! });
  assert.equal(state.phase, "conflict");
  assert.equal(state.draftContent, "My unsaved draft");
  assert.equal(state.baseContent, "Saved 2");
  assert.equal(state.conflictLatest?.content, "Latest remote edit");
  state = artifactWorkspaceReducer(state, { type: "conflict.use-latest" });
  assert.equal(state.phase, "preview");
  assert.equal(state.draftContent, "Latest remote edit");
  assert.equal(selectedArtifact(state)?.content, "Latest remote edit");
});

test("a new Run Artifact never inherits the open editor draft", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Draft for A" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(1, "Generated B", "art_2"), select: true });
  assert.equal(state.selectedId, "art_1");
  assert.equal(state.draftContent, "Draft for A");
  assert.equal(state.resources.some((item) => item.id === "art_2"), true);
  state = artifactWorkspaceReducer(state, { type: "edit.cancelled" });
  assert.equal(state.selectedId, "art_2", "finishing the editor may then reveal the newly generated Artifact");
});

test("saving A completes against A before a pending B becomes active", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Saved only to A" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(1, "Generated B", "art_2"), select: true });
  state = artifactWorkspaceReducer(state, { type: "save.started" });
  state = artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(3, "Saved only to A"), scope: artifactEditorScope(state)! });
  assert.equal(state.phase, "saved");
  assert.equal(state.selectedId, "art_1");
  assert.equal(selectedArtifact(state)?.content, "Saved only to A");
  state = artifactWorkspaceReducer(state, { type: "saved.settled", scope: artifactEditorScope(state)! });
  assert.equal(state.selectedId, "art_2");
  assert.equal(selectedArtifact(state)?.content, "Generated B");
});

test("a late list response merges resources without resetting an active editor", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resource.received", resource: resource(2), select: true });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Unsaved while loading" });
  state = artifactWorkspaceReducer(state, { type: "resources.loaded", resources: [resource(2), resource(1, "Other", "art_2")] });
  assert.equal(state.phase, "editing");
  assert.equal(state.selectedId, "art_1");
  assert.equal(state.draftContent, "Unsaved while loading");
  assert.equal(state.resources.length, 2);
  assert.equal(state.baseVersion, 2);
});

test("dirty compares the immutable editor baseline, never a background-updated resource", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(3, "Remote") });
  assert.equal(artifactDraftHasChanges(state), false, "remote edits alone are not local changes");
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Remote" });
  assert.equal(artifactDraftHasChanges(state), true, "local changes remain dirty even when equal to latest remote");
  assert.equal(state.baseContent, "Saved 2");
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Saved 2" });
  assert.equal(artifactDraftHasChanges(state), false);
});

test("keep mine rebases without saving and a second conflict keeps local changes", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Mine" });
  const obsolete = artifactEditorScope(state)!;
  state = artifactWorkspaceReducer(state, { type: "save.conflict", latest: resource(3, "Theirs"), message: "Conflict", scope: obsolete });
  state = artifactWorkspaceReducer(state, { type: "conflict.keep-mine" });
  assert.equal(state.phase, "editing");
  assert.equal(state.baseVersion, 3);
  assert.equal(state.baseContent, "Theirs");
  assert.equal(state.draftContent, "Mine");
  assert.equal(selectedArtifact(state)?.content, "Theirs", "rebase does not mutate server resource");
  assert.equal(artifactDraftHasChanges(state), true);
  assert.equal(artifactWorkspaceReducer(state, { type: "conflict.refreshed", resource: resource(4), scope: obsolete }), state);
  state = artifactWorkspaceReducer(state, { type: "save.started" });
  state = artifactWorkspaceReducer(state, { type: "save.conflict", latest: resource(4, "Changed again"), message: "Conflict again", scope: artifactEditorScope(state)! });
  assert.equal(state.phase, "conflict");
  assert.equal(state.draftContent, "Mine");
  assert.equal(state.baseVersion, 3);
});

test("restored drafts retain original baseline and conflict against newer saved content", () => {
  const draft = { baseVersion: 2, baseTitle: "Risk brief", baseContent: "Saved 2", draftTitle: "Recovered title", draftContent: "Recovered text" };
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(3, "Remote")] });
  state = artifactWorkspaceReducer(state, { type: "draft.restored", resource: resource(3, "Remote"), draft });
  assert.equal(state.phase, "conflict");
  assert.equal(state.draftContent, "Recovered text");
  assert.equal(state.baseContent, "Saved 2");
  assert.equal(state.conflictLatest?.version, 3);
  assert.equal(artifactDraftHasChanges(state), true);
  assert.equal(artifactWorkspaceReducer(state, { type: "draft.restored", resource: resource(3), draft: { ...draft, draftContent: "Late recovery" } }), state);
  const unchanged = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  assert.equal(artifactWorkspaceReducer(unchanged, { type: "draft.restored", resource: resource(2), draft }).phase, "editing");
  assert.equal(artifactWorkspaceReducer(unchanged, { type: "draft.restored", resource: resource(2, "Other", "art_2"), draft }), unchanged);
});

test("late save and refresh results cannot enter another artifact or a reopened editor", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2), resource(1, "B", "art_2")] });
  state = artifactWorkspaceReducer(state, { type: "resource.selected", id: "art_1" });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  const scope = artifactEditorScope(state)!;
  state = artifactWorkspaceReducer(state, { type: "edit.cancelled" });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "New editor draft" });
  const late = [
    { type: "save.succeeded" as const, resource: resource(3), scope },
    { type: "save.failed" as const, message: "Old failure", scope },
    { type: "save.conflict" as const, latest: resource(3), message: "Old conflict", scope },
    { type: "conflict.refreshed" as const, resource: resource(3), scope },
    { type: "saved.settled" as const, scope },
  ];
  for (const action of late) assert.equal(artifactWorkspaceReducer(state, action), state);
  state = artifactWorkspaceReducer(state, { type: "resource.selected", id: "art_2" });
  for (const action of late) assert.equal(artifactWorkspaceReducer(state, action), state);
});

test("async results reject foreign resource identity even with a current editor token", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  const scope = artifactEditorScope(state)!;
  for (const foreign of [{ ...resource(3), workspace_id: "other" }, { ...resource(3), thread_id: "other" }, resource(3, "B", "art_2")]) {
    assert.equal(artifactWorkspaceReducer(state, { type: "save.succeeded", resource: foreign, scope }), state);
    assert.equal(artifactWorkspaceReducer(state, { type: "save.conflict", latest: foreign, message: "Conflict", scope }), state);
    assert.equal(artifactWorkspaceReducer(state, { type: "conflict.refreshed", resource: foreign, scope }), state);
  }
});

test("late refresh cannot regress a more recent conflict and pending B stays deferred", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Mine" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(1, "B", "art_2"), select: true });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(4, "Newest") });
  state = artifactWorkspaceReducer(state, { type: "conflict.refreshed", resource: resource(3, "Older"), scope: artifactEditorScope(state)! });
  assert.equal(state.conflictLatest?.version, 4);
  assert.equal(state.pendingSelectedId, "art_2");
  state = artifactWorkspaceReducer(state, { type: "conflict.keep-mine" });
  assert.equal(state.selectedId, "art_1");
  state = artifactWorkspaceReducer(state, { type: "save.started" });
  state = artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(5, "Mine"), scope: artifactEditorScope(state)! });
  assert.equal(state.selectedId, "art_1");
  state = artifactWorkspaceReducer(state, { type: "saved.settled", scope: artifactEditorScope(state)! });
  assert.equal(state.selectedId, "art_2");
});

test("a successful save receipt cannot hide a newer remote change", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "Mine" });
  const scope = artifactEditorScope(state)!;
  state = artifactWorkspaceReducer(state, { type: "save.started" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(4, "Remote after my save") });
  state = artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(3, "Mine"), scope });
  assert.equal(state.phase, "conflict");
  assert.equal(state.baseVersion, 3);
  assert.equal(state.draftContent, "Mine");
  assert.equal(state.conflictLatest?.version, 4);
  assert.equal(selectedArtifact(state)?.version, 4);
  assert.equal(artifactDraftHasChanges(state), false, "the acknowledged local text was saved");
});

test("thread reset invalidates earlier editor tokens even when the same artifact is loaded again", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  const scope = artifactEditorScope(state)!;
  state = artifactWorkspaceReducer(state, { type: "thread.reset" });
  state = artifactWorkspaceReducer(state, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  assert.equal(artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(3), scope }), state);
});

test("Task artifact refresh hints are thread-scoped and stable across unrelated progress", () => {
  const task: Task = {
    id: "task_1", workspace_id: "ws_1", thread_id: "thr_1", goal: "Create a document", status: "running", revision: 1,
    created_at: "2026-09-12T00:00:00Z", updated_at: "2026-09-12T00:00:00Z",
    plan: { id: "plan_1", task_id: "task_1", updated_at: "2026-09-12T00:00:00Z", steps: [] },
  };
  const initial = taskArtifactRefreshKey(task, "thr_1");
  assert.equal(taskArtifactRefreshKey(task, "thr_other"), null);
  assert.equal(taskArtifactRefreshKey({ ...task, revision: 2, status: "paused" }, "thr_1"), initial);
  assert.notEqual(taskArtifactRefreshKey({ ...task, status: "completed" }, "thr_1"), initial);
  const evidence = { id: "ev_1", task_id: task.id, step_id: "step_1", kind: "artifact" as const, label: "Document", summary: "Summary only", resource_id: "art_1", created_at: task.created_at };
  const withArtifact = taskArtifactRefreshKey({ ...task, evidence: [evidence] }, "thr_1");
  assert.notEqual(withArtifact, initial);
  assert.equal(taskArtifactRefreshKey({ ...task, evidence: [evidence, evidence] }, "thr_1"), withArtifact);
});
