import assert from "node:assert/strict";
import test from "node:test";

import type { ArtifactResource } from "@alcuin/contracts";

import {
  artifactDraftHasChanges,
  artifactWorkspaceReducer,
  initialArtifactWorkspaceState,
  selectedArtifact,
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
  state = artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(3, "Local edit") });
  assert.equal(state.phase, "saved");
  assert.equal(selectedArtifact(state)?.content, "Local edit");
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", title: "Unsaved" });
  state = artifactWorkspaceReducer(state, { type: "edit.cancelled" });
  assert.equal(state.phase, "preview");
  assert.equal(state.draftTitle, "Risk brief");
});

test("a conflict preserves the draft until the operator refreshes latest", () => {
  let state = artifactWorkspaceReducer(initialArtifactWorkspaceState, { type: "resources.loaded", resources: [resource(2)] });
  state = artifactWorkspaceReducer(state, { type: "edit.started" });
  state = artifactWorkspaceReducer(state, { type: "draft.changed", content: "My unsaved draft" });
  state = artifactWorkspaceReducer(state, { type: "resource.received", resource: resource(3, "Remote edit") });
  assert.equal(state.phase, "conflict");
  assert.equal(state.draftContent, "My unsaved draft");
  state = artifactWorkspaceReducer(state, { type: "conflict.refreshed", resource: resource(4, "Latest remote edit") });
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
  state = artifactWorkspaceReducer(state, { type: "save.succeeded", resource: resource(3, "Saved only to A") });
  assert.equal(state.phase, "saved");
  assert.equal(state.selectedId, "art_1");
  assert.equal(selectedArtifact(state)?.content, "Saved only to A");
  state = artifactWorkspaceReducer(state, { type: "saved.settled" });
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
});
