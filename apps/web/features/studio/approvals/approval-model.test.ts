import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent } from "@alcuin/contracts";
import { approvalRecords, argumentFields } from "./approval-model.ts";

const event = (sequence: number, type: string, payload: Record<string, unknown>): ExecutionEvent => ({ id: `e${sequence}`, run_id: "run1", sequence, type, payload, timestamp: "2026-09-22T01:00:00Z" });
const required = event(1, "approval.required", { approval_id: "a1", tool: "records.update", call_id: "call1", arguments: { record_id: "R-1", status: "ready" } });
const decided = event(2, "approval.decided", { approval_id: "a1", decision: "approved", note: "Reviewed with the owner" });

test("approval and execution are distinct; the result must match the call", () => {
  assert.equal(approvalRecords([required], "waiting_for_approval")[0].state, "pending");
  assert.equal(approvalRecords([required, decided], "running")[0].state, "approved");
  const wrong = event(3, "tool.completed", { tool: "records.update", call_id: "different", status: "succeeded" });
  assert.equal(approvalRecords([required, decided, wrong], "running")[0].state, "approved");
  const failed = event(4, "tool.completed", { call_id: "call1", status: "failed" });
  const record = approvalRecords([failed, required, decided], "failed")[0];
  assert.equal(record.state, "failed");
  assert.equal(record.decision, "approved");
  assert.equal(record.note, "Reviewed with the owner");
});

test("completed Runs and missing receipts cannot be presented as approval success", () => {
  assert.equal(approvalRecords([required], "failed")[0].state, "unavailable");
  assert.equal(approvalRecords([required, decided], "completed")[0].state, "unavailable");
  const done = event(3, "tool.completed", { call_id: "call1", status: "succeeded" });
  assert.equal(approvalRecords([required, decided, done], "completed")[0].state, "succeeded");
});

test("denial remains visible after completion, and unrelated decisions are ignored", () => {
  const denial = event(2, "approval.decided", { approval_id: "a1", decision: "denied", note: "Wrong record" });
  const unrelated = event(3, "approval.decided", { approval_id: "unknown", decision: "approved" });
  const records = approvalRecords([required, denial, unrelated], "completed");
  assert.equal(records.length, 1);
  assert.equal(records[0].state, "denied");
  assert.equal(records[0].note, "Wrong record");
});

test("legacy Run receipts are useful without inventing a decision or identity", () => {
  const done = event(3, "tool.completed", { call_id: "call1", status: "succeeded" });
  const record = approvalRecords([required, done], "completed")[0];
  assert.equal(record.state, "succeeded");
  assert.equal(record.decision, undefined);
});

test("argument fields preserve content, including nested values and newlines", () => {
  assert.deepEqual(argumentFields({ record_id: "a\nb", details: { target: "x" }, enabled: false }), [
    { label: "record id", value: "a\nb" }, { label: "details", value: '{"target":"x"}' }, { label: "enabled", value: "false" },
  ]);
});
