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
        { type: "attachment", attachment_id: "att_1", name: "map.png", media_type: "image/png" },
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
    name: "map.png",
    mediaType: "image/png",
  }]);
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
