import assert from "node:assert/strict";
import test from "node:test";

import type { Task, TaskEvent } from "@alcuin/contracts";

import { initialTaskProjection, projectTaskEvents } from "./task-projection.ts";
import { initialTaskSessionState, taskSessionReducer } from "./task-session-reducer.ts";

const timestamp = "2026-08-30T00:00:00Z";

function task(status: Task["status"] = "ready"): Task {
  return {
    id: "tsk_1",
    workspace_id: "ws_1",
    thread_id: "thr_1",
    goal: "Produce an evidence-backed incident brief",
    status,
    plan: {
      id: "plan_1",
      task_id: "tsk_1",
      updated_at: timestamp,
      steps: [
        {
          id: "step_1",
          task_id: "tsk_1",
          ordinal: 0,
          title: "Collect evidence",
          description: "Read the approved sources.",
          status: "pending",
          attempts: [],
          evidence: [],
        },
        {
          id: "step_2",
          task_id: "tsk_1",
          ordinal: 1,
          title: "Write brief",
          description: "Synthesize the findings.",
          status: "pending",
          attempts: [],
          evidence: [],
        },
      ],
    },
    current_step_id: null,
    result: null,
    latest_checkpoint: null,
    pending_intervention: null,
    revision: 1,
    created_at: timestamp,
    updated_at: timestamp,
  };
}

function event(
  sequence: number,
  type: TaskEvent["type"],
  payload: Record<string, unknown> = {},
  stepId: string | null = null,
): TaskEvent {
  return {
    id: `event_${sequence}_${type}`,
    workspace_id: "ws_1",
    task_id: "tsk_1",
    sequence,
    type,
    timestamp: `2026-08-30T00:00:${String(sequence).padStart(2, "0")}Z`,
    step_id: stepId,
    payload,
  };
}

test("projects overlapping and out-of-order SSE pages exactly once", () => {
  const page = [
    event(3, "task.step.completed", {}, "step_1"),
    event(1, "task.started"),
    event(2, "task.step.started", {}, "step_1"),
  ];
  const projected = projectTaskEvents(initialTaskProjection(task()), page);
  assert.deepEqual(projected.events.map((item) => item.sequence), [1, 2, 3]);
  assert.equal(projected.task?.status, "running");
  assert.equal(projected.task?.plan.steps[0]?.status, "completed");
  assert.equal(projected.progress.percent, 50);

  const replayed = projectTaskEvents(projected, [page[1], page[0]]);
  assert.strictEqual(replayed, projected, "an overlapping reconnect page is an idempotent no-op");
});

test("a later stale step event cannot move progress backwards", () => {
  const projected = projectTaskEvents(initialTaskProjection(task()), [
    event(1, "task.started"),
    event(2, "task.step.completed", {}, "step_1"),
    event(3, "task.step.started", {}, "step_1"),
  ]);
  assert.equal(projected.task?.plan.steps[0]?.status, "completed");
  assert.equal(projected.progress.completed, 1);
  assert.equal(projected.progress.percent, 50);
});

test("terminal task state absorbs late running and waiting events", () => {
  const projected = projectTaskEvents(initialTaskProjection(task("running")), [
    event(1, "task.step.completed", {}, "step_1"),
    event(2, "task.step.completed", {}, "step_2"),
    event(3, "task.completed", { result: { content: "Done", content_type: "text/markdown" } }),
    event(4, "task.resumed"),
    event(5, "task.waiting_for_user", {}, "step_2"),
  ]);
  assert.equal(projected.task?.status, "completed");
  assert.equal(projected.terminalSequence, 3);
  assert.equal(projected.progress.percent, 100);
});

test("terminal event absorbs a late step event without corrupting the plan", () => {
  const projected = projectTaskEvents(initialTaskProjection(task("running")), [
    event(1, "task.step.completed", {}, "step_1"),
    event(2, "task.completed", { result: { content: "Done", content_type: "text/markdown" } }),
    event(3, "task.step.started", {}, "step_2"),
  ]);
  assert.equal(projected.task?.status, "completed");
  assert.equal(projected.task?.plan.steps[1]?.status, "pending");
  assert.equal(projected.terminalSequence, 2);
  assert.equal(projected.lastSequence, 3, "the reconnect cursor still advances past an absorbed frame");
});

test("evidence and interventions replay into operator-safe projections", () => {
  const projected = projectTaskEvents(initialTaskProjection(task("running")), [
    event(1, "task.evidence.added", {
      evidence: {
        id: "evidence_1",
        task_id: "tsk_1",
        step_id: "step_1",
        kind: "citation",
        label: "Incident timeline",
        summary: "Primary timeline source",
        source_uri: "https://example.com/timeline",
        created_at: timestamp,
      },
    }, "step_1"),
    event(2, "task.intervention.queued", {
      intervention: {
        id: "intervention_1",
        task_id: "tsk_1",
        kind: "steer",
        status: "pending",
        message: "Use the corrected incident window.",
        created_at: timestamp,
      },
    }),
    event(3, "task.intervention.applied", {
      intervention: {
        id: "intervention_1",
        task_id: "tsk_1",
        kind: "steer",
        status: "applied",
        message: "Use the corrected incident window.",
        created_at: timestamp,
      },
    }),
  ]);
  assert.equal(projected.task?.evidence.length, 1);
  assert.equal(projected.task?.plan.steps[0]?.evidence.length, 1);
  assert.equal(projected.task?.pending_intervention, null);
});

test("step completion payload evidence updates the cross-step Evidence view", () => {
  const projected = projectTaskEvents(initialTaskProjection(task("running")), [
    event(1, "task.step.completed", {
      evidence: [{
        id: "evidence_from_step",
        task_id: "tsk_1",
        step_id: "step_1",
        kind: "tool_result",
        label: "Search result",
        summary: "Verified result",
        created_at: timestamp,
      }],
    }, "step_1"),
  ]);
  assert.equal(projected.task?.plan.steps[0]?.evidence.length, 1);
  assert.equal(projected.task?.evidence[0]?.id, "evidence_from_step");
});

test("session reducer preserves projection across reconnect and de-duplicates commands", () => {
  let state = taskSessionReducer(initialTaskSessionState, { type: "hydrate.completed", task: task() });
  state = taskSessionReducer(state, { type: "stream.disconnected" });
  assert.equal(state.phase, "reconnecting");
  state = taskSessionReducer(state, { type: "events.received", events: [event(1, "task.started")] });
  assert.equal(state.phase, "ready");
  state = taskSessionReducer(state, { type: "command.requested", pending: { id: "cmd_1", command: "pause" } });
  const duplicate = taskSessionReducer(state, { type: "command.requested", pending: { id: "cmd_1", command: "pause" } });
  assert.strictEqual(duplicate, state);
  assert.equal(state.pendingCommands.length, 1);
});
