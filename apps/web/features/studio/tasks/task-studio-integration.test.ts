import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const studio = readFileSync(new URL("../studio-view.tsx", import.meta.url), "utf8");
const hook = readFileSync(new URL("./use-task-session.ts", import.meta.url), "utf8");
const inlineStatus = readFileSync(new URL("./task-inline-status.tsx", import.meta.url), "utf8");
const globals = readFileSync(new URL("../../../app/globals.css", import.meta.url), "utf8");

test("Task surfaces are state-driven and absent from ordinary conversation DOM", () => {
  assert.match(inlineStatus, /if \(!task\) return null/);
  assert.match(studio, /\{\(taskSession\.task \|\| hasProposal\) && <button id="canvas-tab-task"/);
  assert.match(studio, /\{\(taskSession\.task \|\| hasProposal\) && <div id="canvas-panel-task"/);
  assert.match(studio, /hasProposal \? <TaskProposalPanel/);
});

test("Task commands refresh canonical CAS state and use unique idempotency keys", () => {
  assert.ok(hook.match(/await alcuinApi\.getTask\(task\.id\)/g)?.length ?? 0 >= 3);
  assert.match(hook, /idempotency_key: commandId/);
  assert.match(hook, /expected_revision: canonical\.revision/);
  assert.match(hook, /expected_status: canonical\.status/);
  assert.match(hook, /crypto\.randomUUID\(\)/);
  assert.match(hook, /stateRef\.current\.projection\.lastSequence/);
  assert.match(hook, /controller\.signal/);
});

test("Task create and plan update strip client-only step identity", () => {
  assert.match(hook, /steps: steps \?\? \[\{ title, description: value \}\]/);
  assert.match(hook, /steps: draft\.steps\.map\(\(step\) => \(\{\s*title: step\.title\.trim\(\),\s*\.\.\.\(step\.description\.trim\(\) \? \{ description: step\.description\.trim\(\) \} : \{\}\),\s*\}\)\)/);

  const updatePayload = hook.slice(
    hook.indexOf("const snapshot = await alcuinApi.updateTaskPlan"),
    hook.indexOf("commit({ type: \"plan.save.completed\""),
  );
  assert.doesNotMatch(updatePayload, /\bid\s*:/);
  assert.doesNotMatch(updatePayload, /\bordinal\s*:/);
});

test("Task guidance never silently consumes composer attachments", () => {
  const taskSubmit = studio.slice(
    studio.indexOf("if (activeTask) {"),
    studio.indexOf("if (blocked || taskHydrating) return;"),
  );
  assert.match(taskSubmit, /attachmentController\.items\.length > 0/);
  assert.match(taskSubmit, /showToast\(taskAttachmentMessage\)/);
  assert.doesNotMatch(taskSubmit, /clearAccepted|remove\(/);
});

test("manual Canvas selection wins over artifact auto-open", () => {
  assert.ok(studio.match(/userSelectedCanvasRef\.current/g)?.length ?? 0 >= 5);
  assert.match(studio, /if \(!artifactEnabled \|\| !artifactBelongsToLatestTurn \|\| userSelectedCanvasRef\.current\) return/);
  assert.doesNotMatch(studio, /liveArtifactStarted/);
});

test("Task motion is loaded and the root layout cannot create a blank overflow row", () => {
  assert.match(globals, /@import "\.\.\/features\/studio\/tasks\/task-motion\.css"/);
  const frame = globals.match(/\.app-frame\s*\{[^}]+\}/)?.[0] ?? "";
  const workspace = globals.match(/\.workspace-frame\s*\{[^}]+\}/)?.[0] ?? "";
  assert.match(frame, /grid-template-rows:\s*52px minmax\(0, 1fr\)/);
  assert.match(frame, /height:\s*100dvh/);
  assert.match(workspace, /min-height:\s*0/);
  assert.match(workspace, /overflow:\s*hidden/);
});
