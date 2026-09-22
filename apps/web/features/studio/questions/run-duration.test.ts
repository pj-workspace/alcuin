import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent } from "@alcuin/contracts";
import { activeRunDuration } from "../run-duration.ts";

function event(type: ExecutionEvent["type"], sequence: number, seconds: number): ExecutionEvent {
  return { id: `e${sequence}`, run_id: "r", type, sequence, timestamp: new Date(seconds * 1000).toISOString(), payload: { input_id: "i", approval_id: "a" } };
}
test("time spent answering is not displayed as model thinking", () => {
  const start = event("run.started", 1, 100);
  const ask = event("input.required", 2, 102);
  const answer = event("input.answered", 3, 162);
  const finish = event("run.completed", 4, 165);
  assert.equal(activeRunDuration([start, ask], 160_000), 2);
  assert.equal(activeRunDuration([start, ask, ask, answer], 164_000), 4);
  assert.equal(activeRunDuration([finish, ask, start, answer]), 5);
});
test("approval waits and interrupted unanswered questions are excluded too", () => {
  assert.equal(activeRunDuration([event("run.started", 1, 100), event("approval.required", 2, 101), event("approval.decided", 3, 120), event("run.completed", 4, 122)]), 3);
  assert.equal(activeRunDuration([event("run.started", 1, 100), event("input.required", 2, 102), event("run.failed", 3, 160)]), 2);
  assert.equal(activeRunDuration([]), 0);
});
