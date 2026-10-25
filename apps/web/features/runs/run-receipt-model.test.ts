import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent, Run } from "@alcuin/contracts";
import { buildRunReceipt, collectReceiptArtifacts, exportRunReceiptMarkdown, filterRuns, receiptEvents, runDurationMs } from "./run-receipt-model.ts";

const run: Run = { id: "r1", workspace_id: "w", thread_id: "t", agent_version_id: "v", status: "completed", input: "private prompt", title: "Research", created_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:03Z" };
function event(sequence: number, type: string, payload: Record<string, unknown> = {}, run_id = "r1"): ExecutionEvent {
  return { id: `e${sequence}`, run_id, sequence, type, payload, timestamp: "2026-01-01T00:00:01Z" };
}

test("duration requires actual valid timestamps and accepts injected active time", () => {
  assert.equal(runDurationMs(run), 3000);
  assert.equal(runDurationMs({ ...run, completed_at: null }), null);
  assert.equal(runDurationMs({ ...run, created_at: "invalid" }), null);
  assert.equal(runDurationMs({ ...run, completed_at: "2025-01-01" }), null);
  assert.equal(runDurationMs({ ...run, status: "running", completed_at: null }), null);
  assert.equal(runDurationMs({ ...run, status: "running", completed_at: null }, Date.parse(run.created_at) + 5000), 5000);
});

test("receipt owns its run, sorts and deduplicates identities", () => {
  const first = event(1, "run.started");
  const events = [event(2, "run.completed"), event(0, "tool.completed", { tool: "foreign" }, "other"), first, first];
  assert.deepEqual(receiptEvents(run.id, events).map((item) => item.id), ["e1", "e2"]);
  assert.deepEqual(buildRunReceipt(run, events).items.map((item) => item.label), ["Work started", "Work completed"]);
});

test("approval is permission, never proof of execution; calls pair by exact ID", () => {
  const events = [event(1, "tool.requested", { call_id: "one", tool: "write" }),
    event(2, "approval.required", { approval_id: "a", call_id: "one", tool: "write" }),
    event(3, "approval.decided", { approval_id: "a", decision: "approved" }),
    event(4, "tool.completed", { call_id: "two", tool: "write", status: "succeeded" })];
  const receipt = buildRunReceipt({ ...run, status: "cancelled" }, events);
  assert.deepEqual(receipt.tools, [{ callId: "one", name: "write", status: "unknown", permission: "approved" }, { callId: "two", name: "write", status: "succeeded" }]);
  assert.equal(receipt.items.find((item) => item.label === "Permission granted")?.status, "approved");
  assert.equal(buildRunReceipt(run, [...events, event(5, "tool.completed", { call_id: "one", tool: "write", status: "failed" })]).tools[0].status, "failed");
});

test("cancellation preserves confirmed results but leaves unresolved writes unknown", () => {
  const events = [event(1, "tool.requested", { call_id: "a", tool: "write" }), event(2, "tool.completed", { call_id: "b", status: "succeeded" })];
  for (const status of ["cancel_requested", "cancelled"] as const) {
    assert.deepEqual(buildRunReceipt({ ...run, status }, events).tools.map((tool) => tool.status), ["unknown", "succeeded"]);
  }
});

test("safe projection/export omits transcript, arguments, results and unsafe links", () => {
  const events = [event(1, "reasoning.delta", { delta: "SECRET_REASONING" }), event(2, "message.delta", { delta: "SECRET_MESSAGE" }),
    event(3, "tool.completed", { call_id: "a", tool: "lookup", status: "succeeded", arguments: { secret: "SECRET_ARGUMENT" }, result_summary: "SECRET_RESULT" }),
    event(4, "citation.created", { citation_id: "c1", label: "Unsafe", locator: "javascript:alert(1)", snippet: "SECRET_SNIPPET" }),
    event(5, "citation.created", { citation_id: "c2", label: "Safe", locator: "https://example.org/a" }),
    event(6, "citation.created", { citation_id: "c3", locator: "https://user:password@example.org" }),
    event(7, "unknown.event", { summary: "SECRET_UNKNOWN" })];
  const receipt = buildRunReceipt(run, events);
  assert.equal(receipt.items.length, 4);
  assert.deepEqual(receipt.citations.map((citation) => citation.url), [null, "https://example.org/a", null]);
  const output = exportRunReceiptMarkdown(run, events);
  assert.doesNotMatch(output + JSON.stringify(receipt), /SECRET_|private prompt|javascript:|password/);
  assert.match(output, /https:\/\/example.org\/a/);
  assert.doesNotMatch(exportRunReceiptMarkdown({ ...run, title: "[x](javascript:bad)\n# H" }, []), /Title: \[x\]/);
});

test("search and status filters are functional", () => {
  const rows = [run, { ...run, id: "r2", status: "waiting_for_input" as const, agent_name: "Planner" }];
  assert.equal(filterRuns(rows, "planner").length, 1);
  assert.equal(filterRuns(rows, "", "needs_attention")[0].id, "r2");
  assert.equal(filterRuns(rows, "", "completed")[0].id, "r1");
  assert.equal(filterRuns(rows, "missing", "all").length, 0);
});

test("artifact receipts use canonical nested title without content; skipped input is explicit", () => {
  const receipt = buildRunReceipt(run, [event(1, "artifact.updated", { artifact: { id: "a", workspace_id: "w", thread_id: "t", source_run_id: "r1", title: "Report", content: "PRIVATE_BODY" } }), event(2, "input.answered", { skip: true, answer: "PRIVATE_ANSWER" })]);
  assert.equal(receipt.items[0].name, "Report");
  assert.equal(receipt.items[1].label, "Your input skipped");
  assert.doesNotMatch(JSON.stringify(receipt), /PRIVATE_/);
});

test("explicit event workspace must match; legacy scoped events remain readable", () => {
  const wrong = { ...event(1, "tool.completed", { call_id: "a", tool: "foreign", status: "succeeded" }), workspace_id: "foreign" };
  const malformed = { ...event(2, "run.failed"), workspace_id: null };
  const own = { ...event(3, "run.started"), workspace_id: "w" };
  assert.deepEqual(buildRunReceipt(run, [wrong, malformed, own, event(4, "run.completed")]).items.map((item) => item.id), ["e3", "e4"]);
  assert.doesNotMatch(exportRunReceiptMarkdown(run, [wrong]), /foreign/);
});

test("artifacts require complete ownership and latest version without private bodies", () => {
  const artifact = { id: "a", workspace_id: "w", thread_id: "t", source_run_id: "r1", title: "Current", kind: "report", version: 2, content: "PRIVATE_HTML" };
  const events = [event(1, "artifact.updated", { artifact }),
    event(2, "artifact.updated", { artifact: { ...artifact, version: 1, title: "Stale" } }),
    event(3, "artifact.updated", { artifact: { ...artifact, title: "Foreign", thread_id: "other" } }),
    event(4, "artifact.updated", { artifact: { ...artifact, title: "Foreign", workspace_id: "other" } }),
    event(5, "artifact.updated", { artifact: { ...artifact, title: "Foreign", source_run_id: "other" } }),
    event(6, "artifact.updated", { artifact: { id: "missing", title: "Missing ownership" } }),
    event(7, "artifact.updated", { artifact: { ...artifact, title: "Latest" } })];
  assert.deepEqual(collectReceiptArtifacts(run, events), [{ id: "a", title: "Latest", kind: "report" }]);
  assert.doesNotMatch(JSON.stringify(buildRunReceipt(run, events)), /Foreign|Missing ownership|PRIVATE_HTML/);
});

test("export translates fixed copy and states, preserving supplied names", () => {
  const translated: string[] = [];
  const output = exportRunReceiptMarkdown(run, [event(1, "tool.completed", { call_id: "a", tool: "Named tool", status: "failed" }), event(2, "citation.created", { citation_id: "c", label: "Named source", locator: "https://example.org" })], (value) => { translated.push(value); return `translated ${value}`; });
  assert.match(output, /translated Work receipt/);
  assert.match(output, /translated completed/);
  assert.match(output, /Named tool: translated failed/);
  assert.match(output, /translated Open source/);
  for (const literal of ["Research", "Named tool", "Named source"]) assert.equal(translated.includes(literal), false);
});

test("export identifies distinct runs with a shared thread and escapes identifiers", () => {
  const first = exportRunReceiptMarkdown({ ...run, id: "run_[one]", thread_id: "thread_[shared]" }, [], (value) => value === "Run ID" ? "运行标识" : value === "Thread ID" ? "会话标识" : value);
  const second = exportRunReceiptMarkdown({ ...run, id: "run_[two]", thread_id: "thread_[shared]" }, []);
  assert.ok(first.includes("运行标识: run\\_\\[one\\]"));
  assert.ok(first.includes("会话标识: thread\\_\\[shared\\]"));
  assert.ok(second.includes("Run ID: run\\_\\[two\\]"));
  assert.ok(second.includes("Thread ID: thread\\_\\[shared\\]"));
  assert.notEqual(first, second);
});

test("knowledge citation export retains allowlisted identifiers and locations without bodies", () => {
  const events = [event(1, "citation.created", { citation_id: "cite_[a]", label: "Internal source", locator: "knowledge://internal", snippet: "PRIVATE_SNIPPET", metadata: { kind: "knowledge", document_id: "doc_[a]", page_number: 0, page: 8, chunk_index: 3, content: "PRIVATE_CONTENT", secret: "PRIVATE_SECRET" } })];
  assert.deepEqual(buildRunReceipt(run, events).citations, [{ id: "cite_[a]", title: "Internal source", url: null, documentId: "doc_[a]", pageNumber: 0, chunkIndex: 3 }]);
  const labels: Record<string, string> = { "Citation ID": "引用标识", "Document ID": "文档标识", "Page": "页码", "Chunk index": "分块序号" };
  const output = exportRunReceiptMarkdown(run, events, (value) => labels[value] ?? value);
  assert.ok(output.includes("引用标识: cite\\_\\[a\\]"));
  assert.ok(output.includes("文档标识: doc\\_\\[a\\]"));
  assert.ok(output.includes("页码: 0"));
  assert.ok(output.includes("分块序号: 3"));
  assert.doesNotMatch(output + JSON.stringify(buildRunReceipt(run, events)), /PRIVATE_/);
});

test("citation locations reject invalid metadata types and use valid page alias", () => {
  const invalidValues = ["2", -1, 1.5, NaN, Infinity, {}, null, true];
  for (const value of invalidValues) {
    const receipt = buildRunReceipt(run, [event(1, "citation.created", { citation_id: "c", metadata: { document_id: 12, page_number: value, page: value, chunk_index: value } })]);
    assert.deepEqual(receipt.citations, [{ id: "c", title: "Source", url: null }]);
  }
  assert.equal(buildRunReceipt(run, [event(1, "citation.created", { metadata: { page: 4 } })]).citations[0].pageNumber, 4);
});
