import assert from "node:assert/strict";
import test from "node:test";

import type { ArtifactReferenceMessagePart, ExecutionEvent, ThreadDetail } from "@alcuin/contracts";
import { stopRecoveryDelay } from "./stop-recovery.ts";
import { toolDisplayStatus } from "./run-stop-display.ts";

test("stop trace presentation closes questions without inventing execution results", () => {
  assert.equal(toolDisplayStatus("running", "human.ask", "cancel_requested"), "stopped");
  assert.equal(toolDisplayStatus("running", "human.ask", "cancelled"), "stopped");
  assert.equal(toolDisplayStatus("running", "records.update", "cancel_requested"), "running");
  assert.equal(toolDisplayStatus("running", "knowledge.search", "cancelled"), "stopped");
  assert.equal(toolDisplayStatus("success", "records.update", "cancelled"), "success");
  assert.equal(toolDisplayStatus("error", "records.update", "cancelled"), "error");
  assert.equal(toolDisplayStatus("running", "human.ask", "waiting_for_input"), "running");
});

test("stop recovery reaches a finite retry boundary", () => {
  assert.deepEqual([0, 1, 2, 3, 4].map(stopRecoveryDelay), [1000, 2000, 4000, 8000, null]);
});

import {
  initialThreadSessionState,
  isCurrentSubmissionScope,
  optimisticTurn,
  threadSessionReducer,
  turnsFromThreadDetail,
} from "./thread-session-reducer.ts";

const artifactSnapshot: ArtifactReferenceMessagePart = {
  type: "artifact_reference", artifact_id: "art_1", version: 2,
  title: "Saved draft", content_type: "text/markdown", source_run_id: "run_origin",
  source_run_status: "cancelled",
  sha256: "a".repeat(64), content: "Saved version two",
};

test("submission scope rejects navigation and late errors while allowing its created thread", () => {
  const original = { agentId: "agt_1", threadId: null as string | null };
  assert.equal(isCurrentSubmissionScope(original, original, true, null), true);
  original.threadId = "thr_created";
  assert.equal(isCurrentSubmissionScope(original, original, true, "thr_created"), true);
  assert.equal(isCurrentSubmissionScope(original, { agentId: "agt_1", threadId: "thr_other" }, true, "thr_created"), false);
  assert.equal(isCurrentSubmissionScope(original, { ...original }, true, "thr_created"), false, "navigating away and back must not revive an old response");
  assert.equal(isCurrentSubmissionScope(original, original, false, "thr_created"), false);
  assert.equal(isCurrentSubmissionScope(original, original, true, "thr_other"), false);
});

test("canonical references replace only the target turn without disturbing stream state", () => {
  const turn = { ...optimisticTurn("run_1", "Revise", [], [{ ...artifactSnapshot, sha256: "" }]), runId: "run_1", assistantText: "Streaming answer" };
  const other = { ...optimisticTurn("run_2", "Other", []), runId: "run_2" };
  const state = { ...initialThreadSessionState, thread: detail.thread, turns: [turn, other], phase: "streaming" as const, activeRunId: "run_2" };
  const message = { ...detail.messages.find((item) => item.role === "user")!, parts: [{ ...artifactSnapshot }] };
  const next = threadSessionReducer(state, { type: "references.hydrated", threadId: "thr_1", runId: "run_1", message });
  assert.deepEqual(next.turns[0]?.artifactReferences, [artifactSnapshot]);
  assert.equal(next.turns[0]?.assistantText, "Streaming answer");
  assert.equal(next.turns[1], other);
  assert.equal(next.phase, "streaming");
  assert.equal(next.activeRunId, "run_2");
  assert.equal(threadSessionReducer(state, { type: "references.hydrated", threadId: "another_thread", runId: "run_1", message }), state);
  assert.equal(threadSessionReducer(state, { type: "references.hydrated", threadId: "thr_1", runId: "run_2", message }), state);
});

test("optimistic turns retain independent saved artifact snapshots", () => {
  const reference = { ...artifactSnapshot };
  const turn = optimisticTurn("pending_1", "Revise", [], [reference]);
  reference.content = "Later local edits";
  assert.deepEqual(turn.artifactReferences, [artifactSnapshot]);
  assert.deepEqual(optimisticTurn("pending_2", "Next", []).artifactReferences, []);
});

test("history restores artifact snapshots on their own run without following later versions", () => {
  const history = structuredClone(detail);
  const user = history.messages.find((message) => message.role === "user")!;
  user.parts.push({ ...artifactSnapshot });
  history.runs.push({ ...history.runs[0]!, id: "run_2", input: "Next", input_message_id: "msg_next", output_message_id: null, created_at: "2026-08-29T00:01:00Z" });
  history.messages.push({ ...user, id: "msg_next", run_id: "run_2", sequence: 3, parts: [{ type: "text", text: "Next" }], created_at: "2026-08-29T00:01:00Z" });
  const turns = turnsFromThreadDetail(history);
  assert.deepEqual(turns[0]?.artifactReferences, [artifactSnapshot]);
  assert.deepEqual(turns[1]?.artifactReferences, []);
  (user.parts.at(-1) as ArtifactReferenceMessagePart).content = "Mutated after hydration";
  assert.equal(turns[0]?.artifactReferences?.[0]?.content, "Saved version two");
});

test("history restores artifact references even when the run list is paginated", () => {
  const history = structuredClone(detail);
  history.runs = [];
  history.messages.find((message) => message.role === "user")!.parts.push({ ...artifactSnapshot });
  assert.deepEqual(turnsFromThreadDetail(history)[0]?.artifactReferences, [artifactSnapshot]);
});

const detail: ThreadDetail = {
  thread: {
    id: "thr_1",
    workspace_id: "ws_1",
    agent_id: "agt_1",
    agent_version_id: "av_1",
    title: "Persistent thread",
    context: {},
    created_at: "2026-08-29T00:00:00Z",
  },
  runs: [{
    id: "run_1",
    workspace_id: "ws_1",
    thread_id: "thr_1",
    agent_version_id: "av_1",
    input_message_id: "msg_user",
    output_message_id: "msg_agent",
    status: "completed",
    input: "Fallback input",
    created_at: "2026-08-29T00:00:01Z",
  }],
  messages: [
    {
      id: "msg_agent",
      workspace_id: "ws_1",
      thread_id: "thr_1",
      run_id: "run_1",
      agent_version_id: "av_1",
      sequence: 2,
      role: "assistant",
      status: "completed",
      parts: [{ type: "text", text: "Persisted response" }],
      created_at: "2026-08-29T00:00:03Z",
    },
    {
      id: "msg_user",
      workspace_id: "ws_1",
      thread_id: "thr_1",
      run_id: "run_1",
      agent_version_id: "av_1",
      sequence: 1,
      role: "user",
      status: "completed",
      parts: [
        { type: "text", text: "Persisted input" },
        { type: "attachment", attachment_id: "att_1", kind: "image", name: "map.png", media_type: "image/png", size_bytes: 4_096 },
      ],
      created_at: "2026-08-29T00:00:01Z",
    },
  ],
};

test("stop waits for server confirmation, preserves partial content, and survives reload", () => {
  const run = { ...detail.runs[0], status: "running" as const };
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail: { ...detail, runs: [run] }, latestRunId: run.id, latestRunStatus: "running" });
  state = threadSessionReducer(state, { type: "stop.requested", runId: run.id });
  assert.equal(state.phase, "streaming");
  assert.equal(state.turns[0].status, "running");
  state = threadSessionReducer(state, { type: "stop.failed", runId: run.id });
  assert.equal(state.stop?.pending, false);
  assert.ok(state.stop?.error);
  const event: ExecutionEvent = { id: "cancel1", run_id: run.id, sequence: 9, timestamp: "2026-08-29T00:00:05Z", type: "run.cancel_requested", payload: { tool_in_flight: true } };
  state = threadSessionReducer(state, { type: "events.appended", runId: run.id, events: [event] });
  assert.equal(state.stop?.pending, true);
  assert.equal(state.phase, "streaming");
  state = threadSessionReducer(state, { type: "stop.disconnected", runId: run.id });
  assert.equal(state.stop?.pending, false);
  assert.equal(state.turns[0].status, "cancel_requested");
  assert.equal(state.activeRunId, run.id);
  assert.match(state.stop?.error ?? "", /Connection lost/);
  state = threadSessionReducer(state, { type: "events.appended", runId: run.id, events: [{ ...event, id: "cancel2", sequence: 10, type: "run.cancelled" }] });
  assert.equal(state.phase, "idle");
  assert.equal(state.activeRunId, null);
  assert.equal(state.turns[0].assistantText, "Persisted response");
  assert.equal(state.turns[0].status, "cancelled");
  assert.equal(threadSessionReducer(state, { type: "run.settled", runId: run.id, status: "running" }), state);
  const restored = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail: { ...detail, runs: [{ ...run, status: "cancelled" }] }, latestRunId: run.id, latestRunStatus: "cancelled" });
  assert.equal(restored.turns[0].status, "cancelled");
  assert.equal(restored.turns[0].assistantText, "Persisted response");
});

test("stop responses for a previous thread cannot change the current stop state", () => {
  assert.equal(threadSessionReducer(initialThreadSessionState, { type: "stop.failed", runId: "old" }), initialThreadSessionState);
  assert.equal(threadSessionReducer(initialThreadSessionState, { type: "stop.requested", runId: "old" }), initialThreadSessionState);
});

test("paged-out cancelled runs keep their receipt even with an empty assistant message", () => {
  const fallbackDetail: ThreadDetail = { ...detail, runs: [], messages: detail.messages.map((message) => message.role === "assistant"
    ? { ...message, status: "cancelled", parts: [{ type: "text", text: "" }] } : message) };
  const [turn] = turnsFromThreadDetail(fallbackDetail);
  assert.equal(turn.status, "cancelled");
  assert.equal(turn.assistantText, "");
  assert.equal(turn.events.length, 0);
  assert.equal(turn.input, "Persisted input");
});

test("materializes ordered conversation turns and attachment references", () => {
  const turns = turnsFromThreadDetail(detail);
  assert.equal(turns.length, 1);
  assert.equal(turns[0]?.input, "Persisted input");
  assert.equal(turns[0]?.assistantText, "Persisted response");
  assert.deepEqual(turns[0]?.attachments, [{
    id: "att_1",
    kind: "image",
    name: "map.png",
    mediaType: "image/png",
    sizeBytes: 4_096,
  }]);
});

test("historical citation projection survives refresh without mixing Run-local source IDs or duplicating latest events", () => {
  const firstSource: ExecutionEvent = { id: "source-first", run_id: "run_1", sequence: 4, type: "citation.created", timestamp: "2026-08-29T00:00:03Z", payload: { citation_id: "s1", locator: "https://example.com/first" } };
  const latestSource: ExecutionEvent = { ...firstSource, id: "source-latest", run_id: "run_2", payload: { citation_id: "s1", locator: "https://example.com/latest" } };
  const historical: ThreadDetail = {
    ...detail,
    runs: [...detail.runs, { ...detail.runs[0]!, id: "run_2", input_message_id: undefined, output_message_id: undefined, created_at: "2026-08-29T00:01:00Z" }],
    citation_events: [latestSource, firstSource, { ...firstSource, run_id: "run_foreign" }, { ...firstSource, type: "message.delta", payload: { delta: "Must not replace an answer" } }],
  };
  const turns = turnsFromThreadDetail(historical, [latestSource]);
  assert.deepEqual(turns[0]?.events, [firstSource]);
  assert.deepEqual(turns[1]?.events, [latestSource]);
  assert.equal(turns[0]?.assistantText, "Persisted response");
  assert.equal(turns[0]?.events[0]?.payload.locator, "https://example.com/first");
  assert.equal(turns[1]?.events[0]?.payload.locator, "https://example.com/latest");
});

test("keeps optimistic turn identity while attaching a real run and deduplicates replayed events", () => {
  const optimistic = optimisticTurn("local_1", "Hello", []);
  let state = threadSessionReducer(initialThreadSessionState, { type: "turn.submitted", turn: optimistic });
  state = threadSessionReducer(state, { type: "run.created", optimisticId: "local_1", run: detail.runs[0]! });

  const delta: ExecutionEvent = {
    id: "evt_1",
    run_id: "run_1",
    sequence: 1,
    type: "message.delta",
    timestamp: "2026-08-29T00:00:04Z",
    payload: { delta: "Hello" },
  };
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [delta, delta] });
  assert.equal(state.turns[0]?.events.length, 1);
  assert.equal(state.turns[0]?.assistantText, "Hello");
  assert.equal(state.activeRunId, "run_1");
});

test("removes a rejected optimistic turn without destroying hydrated history", () => {
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail });
  state = threadSessionReducer(state, {
    type: "turn.submitted",
    turn: optimisticTurn("local_failed", "Retry me", []),
  });
  state = threadSessionReducer(state, { type: "turn.failed", optimisticId: "local_failed", message: "offline" });
  assert.deepEqual(state.turns.map((turn) => turn.id), ["run_1"]);
  assert.equal(state.error, "offline");
});

test("hydrates an in-flight run as blocked and keeps waiting approval distinct from streaming", () => {
  const runningDetail: ThreadDetail = {
    ...detail,
    runs: [{ ...detail.runs[0]!, status: "running" }],
  };
  let state = threadSessionReducer(initialThreadSessionState, {
    type: "hydrate.completed",
    detail: runningDetail,
    latestRunId: "run_1",
    latestRunStatus: "running",
  });
  assert.equal(state.phase, "streaming");
  assert.equal(state.activeRunId, "run_1");

  state = threadSessionReducer(state, {
    type: "run.settled",
    runId: "run_1",
    status: "waiting_for_approval",
  });
  assert.equal(state.phase, "waiting_for_approval");
  assert.equal(state.activeRunId, "run_1");
});

test("an old stream settling cannot release the currently active run", () => {
  const currentRun = { ...detail.runs[0]!, id: "run_current", status: "running" as const };
  let state = threadSessionReducer(initialThreadSessionState, {
    type: "hydrate.completed",
    detail: { ...detail, runs: [currentRun] },
    latestRunId: currentRun.id,
    latestRunStatus: "running",
  });
  state = threadSessionReducer(state, {
    type: "run.settled",
    runId: "run_previous",
    status: "completed",
  });
  assert.equal(state.phase, "streaming");
  assert.equal(state.activeRunId, currentRun.id);
});

test("switching threads clears the previous timeline while the requested history loads", () => {
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail });
  state = threadSessionReducer(state, { type: "hydrate.started", threadId: "thr_2" });
  assert.equal(state.phase, "loading");
  assert.equal(state.thread, null);
  assert.deepEqual(state.turns, []);
});

function delta(sequence: number, text: string): ExecutionEvent {
  return {
    id: `evt_${sequence}`, run_id: "run_1", sequence,
    type: "message.delta", timestamp: "2026-08-29T00:00:04Z",
    payload: { delta: text },
  };
}

test("ordered streaming only projects new delta payloads and preserves untouched turns", () => {
  let reads = 0;
  const first = delta(1, "Hello");
  Object.defineProperty(first.payload, "delta", { get: () => { reads += 1; return "Hello"; } });
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail });
  state = threadSessionReducer(state, { type: "turn.submitted", turn: optimisticTurn("other", "Next", []) });
  const untouched = state.turns[1];
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [first] });
  const previous = state;
  const readsBefore = reads;
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [delta(2, " world")] });
  assert.equal(state.turns[0]?.assistantText, "Hello world");
  assert.equal(previous.turns[0]?.assistantText, "Hello");
  assert.equal(previous.turns[0]?.events.length, 1);
  assert.equal(state.turns[1], untouched);
  assert.equal(reads, readsBefore, "historical answer deltas should not be projected again");
});

test("empty, foreign and identical replay batches retain the entire session identity", () => {
  const first = delta(1, "Hello");
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail });
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [first] });
  for (const events of [[], [first], [{ ...first, run_id: "foreign" }]]) {
    assert.equal(threadSessionReducer(state, { type: "events.appended", runId: "run_1", events }), state);
  }
  assert.equal(threadSessionReducer(state, { type: "events.appended", runId: "missing", events: [first] }), state);
});

test("out-of-order replay and corrections retain sequence ordering and last-write-wins behavior", () => {
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail });
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [delta(3, "C"), delta(1, "A")] });
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [delta(2, "B"), delta(1, "Corrected")] });
  assert.equal(state.turns[0]?.assistantText, "CorrectedBC");
  assert.deepEqual(state.turns[0]?.events.map((event) => event.sequence), [1, 2, 3]);
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [delta(4, "D"), delta(4, "Latest")] });
  assert.equal(state.turns[0]?.assistantText, "CorrectedBCLatest");
});

test("non-answer events preserve hydrated text until the first answer delta", () => {
  let state = threadSessionReducer(initialThreadSessionState, { type: "hydrate.completed", detail });
  const reasoning: ExecutionEvent = { ...delta(1, "Thinking"), type: "reasoning.delta" };
  const tool: ExecutionEvent = { ...delta(2, ""), type: "tool.requested", payload: { tool: "search", arguments: { query: "test" } } };
  const citation: ExecutionEvent = { ...delta(3, ""), type: "citation.created", payload: { citation_id: "s1", locator: "https://example.com" } };
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [reasoning, tool, citation] });
  assert.equal(state.turns[0]?.assistantText, "Persisted response");
  assert.deepEqual(state.turns[0]?.events, [reasoning, tool, citation]);
  state = threadSessionReducer(state, { type: "events.appended", runId: "run_1", events: [delta(4, "New response")] });
  assert.equal(state.turns[0]?.assistantText, "New response");
});
