import assert from "node:assert/strict";
import test from "node:test";

import {
  createAlcuinClient,
  executionEventFromCanonicalFrame,
  executionEventFromChatFrame,
} from "./index.ts";

function sseResponse(...frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

test("client scopes requests to its configured Workspace", async () => {
  const seen: Array<{ url: string; workspace: string | null }> = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const headers = new Headers(init?.headers);
    seen.push({ url: String(input), workspace: headers.get("X-Alcuin-Workspace") });
    return new Response(JSON.stringify({ workspace: { id: "ws_test" }, agents: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test/",
    workspaceId: "ws_test",
    fetch: fetchMock,
  });

  await client.bootstrap();

  assert.deepEqual(seen, [{
    url: "https://agents.example.test/v1/bootstrap",
    workspace: "ws_test",
  }]);
});

test("client rejects ambiguous connection configuration", () => {
  assert.throws(
    () => createAlcuinClient({ baseUrl: "agents.example.test", workspaceId: "ws_test" }),
    /http or https/,
  );
  assert.throws(
    () => createAlcuinClient({ baseUrl: "https://agents.example.test", workspaceId: " " }),
    /workspaceId is required/,
  );
});

test("client reads Workspace-scoped thread messages and context traces", async () => {
  const seen: string[] = [];
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input);
    seen.push(url);
    if (url.endsWith("/v1/threads/thr_test")) {
      return Response.json({
        thread: { id: "thr_test", workspace_id: "ws_test", agent_id: "agt_test" },
        messages: [],
        runs: [],
      });
    }
    if (url.includes("/v1/threads/thr_test/messages")) return Response.json([]);
    return Response.json({
      id: "ctx_test",
      workspace_id: "ws_test",
      thread_id: "thr_test",
      run_id: "run_test",
      agent_version_id: "agv_test",
      entries: [],
      estimated_input_tokens: 32,
      effective_budget_tokens: 8_192,
      message_sequence_through: 2,
      created_at: "2026-08-29T00:00:00Z",
    });
  };
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: fetchMock,
  });

  const thread = await client.getThread("thr_test");
  const messages = await client.listThreadMessages("thr_test", { after: 4, limit: 25 });
  const context = await client.getRunContext("run_test");

  assert.equal(thread.thread.id, "thr_test");
  assert.deepEqual(messages, []);
  assert.equal(context.id, "ctx_test");
  assert.deepEqual(seen, [
    "https://agents.example.test/v1/threads/thr_test",
    "https://agents.example.test/v1/threads/thr_test/messages?after=4&limit=25",
    "https://agents.example.test/v1/runs/run_test/context",
  ]);
});

test("client exposes immutable Agent versions and exact-version publishing", async () => {
  const seen: Array<{ url: string; method: string; body: unknown }> = [];
  const fetchMock: typeof fetch = async (input, init) => {
    seen.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return Response.json({});
  };
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: fetchMock,
  });
  const definition: Parameters<typeof client.createAgentVersion>[1] = {
    schema_version: "2026-08-28",
    identity: { name: "Versioned Agent", description: "", icon: "spark" },
    instructions: "Keep every Thread pinned to an immutable Agent version.",
    model: { provider: "test", model: "test-model" },
    extensions: [],
    tools: [],
    knowledge: [],
    runtime: { adapter: "test", max_steps: 4 },
    policies: { mutating_tools: "ask", external_side_effects: "ask" },
    context_policy: {},
    output_schema: {},
    starter_prompts: [],
  };

  await client.listAgentVersions("agt/test");
  await client.getAgentVersion("agt/test", "agv/v2");
  await client.createAgentVersion("agt/test", definition);
  await client.publishAgentVersion("agt/test", "agv/v2");
  await client.publishAgent("agt/test");

  assert.deepEqual(seen, [
    {
      url: "https://agents.example.test/v1/agents/agt%2Ftest/versions",
      method: "GET",
      body: undefined,
    },
    {
      url: "https://agents.example.test/v1/agents/agt%2Ftest/versions/agv%2Fv2",
      method: "GET",
      body: undefined,
    },
    {
      url: "https://agents.example.test/v1/agents/agt%2Ftest/versions",
      method: "POST",
      body: { definition },
    },
    {
      url: "https://agents.example.test/v1/agents/agt%2Ftest/versions/agv%2Fv2/publish",
      method: "POST",
      body: undefined,
    },
    {
      url: "https://agents.example.test/v1/agents/agt%2Ftest/publish",
      method: "POST",
      body: undefined,
    },
  ]);
});

test("client can pin a new Thread to an explicit Agent version", async () => {
  const bodies: unknown[] = [];
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: async (_input, init) => {
      bodies.push(JSON.parse(String(init?.body)));
      return Response.json({});
    },
  });

  await client.createThread("agt_test", { record_id: "rec_1" }, "agv_test_v1");
  await client.createThread("agt_test");

  assert.deepEqual(bodies, [
    {
      agent_id: "agt_test",
      title: "Working session",
      context: { record_id: "rec_1" },
      agent_version_id: "agv_test_v1",
    },
    {
      agent_id: "agt_test",
      title: "Working session",
      context: {},
    },
  ]);
});

test("canonical run stream preserves forward-compatible event names", async () => {
  const seen: Array<{ url: string; cursor: string | null }> = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const headers = new Headers(init?.headers);
    seen.push({ url: String(input), cursor: headers.get("Last-Event-ID") });
    return sseResponse(
      'id: 5\nevent: runtime.observation\ndata: {"id":"evt_5","run_id":"run_test","sequence":5,"type":"runtime.observation","timestamp":"2026-08-29T00:00:00Z","payload":{"label":"Observed"}}\n\n',
      'id: 6\nevent: run.completed\ndata: {"id":"evt_6","run_id":"run_test","sequence":6,"type":"run.completed","timestamp":"2026-08-29T00:00:01Z","payload":{"status":"completed"}}\n\n',
    );
  };
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: fetchMock,
  });
  const events: string[] = [];

  await client.streamRun("run_test", (event) => events.push(event.type), 4);

  assert.deepEqual(events, ["runtime.observation", "run.completed"]);
  assert.deepEqual(seen, [{
    url: "https://agents.example.test/v1/runs/run_test/events",
    cursor: "4",
  }]);
});

test("canonical frame validation keeps unknown events and rejects malformed envelopes", () => {
  const event = executionEventFromCanonicalFrame({
    id: "evt_custom",
    run_id: "run_test",
    sequence: 9,
    type: "runtime.custom",
    timestamp: "2026-08-29T00:00:00Z",
    payload: { value: 1 },
  });

  assert.equal(event?.type, "runtime.custom");
  assert.equal(executionEventFromCanonicalFrame({ type: "runtime.custom" }), null);
});

test("explicit chat stream remains available for compatibility", async () => {
  let requestedUrl = "";
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: async (input) => {
      requestedUrl = String(input);
      return sseResponse(
        'id: 1\ndata: {"eventId":"evt_1","runId":"run_test","sequence":1,"timestamp":"2026-08-29T00:00:00Z","type":"text-delta","textDelta":"Hello"}\n\n',
        'id: 2\ndata: {"eventId":"evt_2","runId":"run_test","sequence":2,"timestamp":"2026-08-29T00:00:01Z","type":"done"}\n\n',
      );
    },
  });
  const events: string[] = [];

  await client.streamRunChat("run_test", (event) => events.push(event.type));

  assert.equal(requestedUrl, "https://agents.example.test/v1/runs/run_test/events?protocol=chat");
  assert.deepEqual(events, ["message.delta", "run.completed"]);
});

test("chat frames map back to canonical execution events", () => {
  assert.deepEqual(
    executionEventFromChatFrame({
      eventId: "evt_1",
      runId: "run_1",
      sequence: 3,
      timestamp: "2026-08-28T00:00:00Z",
      type: "tool-result",
      name: "knowledge.search",
      status: "success",
      outputPreview: "Two sources found",
    }),
    {
      id: "evt_1",
      run_id: "run_1",
      sequence: 3,
      timestamp: "2026-08-28T00:00:00Z",
      type: "tool.completed",
      payload: {
        tool: "knowledge.search",
        status: "succeeded",
        result_summary: "Two sources found",
      },
    },
  );
});
