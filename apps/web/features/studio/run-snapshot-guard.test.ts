import assert from "node:assert/strict";
import test from "node:test";
import { boundedRunSnapshot, isCurrentRunScope } from "./run-snapshot-guard.ts";

test("a hung snapshot aborts and rejects without waiting for transport cooperation", async () => {
  let signal: AbortSignal | undefined;
  await assert.rejects(boundedRunSnapshot((value) => {
    signal = value;
    return new Promise<never>(() => {});
  }, 5), { name: "TimeoutError" });
  assert.equal(signal?.aborted, true);
});

test("a delayed snapshot cannot apply after thread navigation", async () => {
  const run = { id: "r1", thread_id: "t1" };
  const scope = { mounted: true, loading: false, threadId: "t1", activeRunId: "r1", latestRunId: "r1" };
  let resolve!: (value: typeof run) => void;
  const pending = boundedRunSnapshot(() => new Promise<typeof run>((done) => { resolve = done; }));
  scope.threadId = "t2";
  scope.activeRunId = "r2";
  scope.latestRunId = "r2";
  resolve(run);
  const snapshot = await pending;
  assert.equal(isCurrentRunScope(scope, snapshot), false);
});

test("scope permits new-thread creation and terminal latest-run refresh without reviving old runs", () => {
  const run = { id: "r1", thread_id: "t1" };
  const scope = { mounted: true, loading: false, threadId: "t1", activeRunId: "r1", latestRunId: "r1" };
  assert.equal(isCurrentRunScope(scope, run), true);
  assert.equal(isCurrentRunScope({ ...scope, activeRunId: null }, run), true);
  assert.equal(isCurrentRunScope({ ...scope, activeRunId: "r2", latestRunId: "r2" }, run), false);
  assert.equal(isCurrentRunScope({ ...scope, mounted: false }, run), false);
  assert.equal(isCurrentRunScope({ ...scope, loading: true }, run), false);
});
