import assert from "node:assert/strict";
import test from "node:test";

import type { ExecutionEvent, ThreadDetail } from "@alcuin/contracts";

import {
  initialThreadSessionState,
  optimisticTurn,
  threadSessionReducer,
  turnsFromThreadDetail,
} from "./thread-session-reducer.ts";

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
