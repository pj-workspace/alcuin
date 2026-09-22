import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent } from "@alcuin/contracts";
import { questionRecords } from "./question-model.ts";

const required: ExecutionEvent = { id: "ev1", run_id: "run1", sequence: 2, timestamp: "2026-09-22T00:00:00Z", type: "input.required", payload: { input_id: "inp1", question: "Audience?", options: ["Engineering", "Management"] } };
const answered: ExecutionEvent = { ...required, id: "ev2", sequence: 3, type: "input.answered", payload: { input_id: "inp1", answer: "Engineering", skip: false } };
test("only a waiting Run offers an answer; replayed frames do not duplicate questions", () => {
  assert.equal(questionRecords([required, required], "waiting_for_input").length, 1);
  assert.equal(questionRecords([required], "waiting_for_input")[0].state, "pending");
  for (const status of ["failed", "completed", "cancelled", "running"] as const) assert.equal(questionRecords([required], status)[0].state, "closed");
});
test("ordered records preserve answers and explicit skipping on refresh", () => {
  assert.equal(questionRecords([answered, required], "completed")[0].answer, "Engineering");
  assert.equal(questionRecords([required, { ...answered, payload: { ...answered.payload, skip: true, answer: "" } }], "running")[0].state, "skipped");
  assert.deepEqual(questionRecords([answered], "completed"), []);
});
