import assert from "node:assert/strict";
import test from "node:test";

import { createAlcuinClient, executionEventFromChatFrame } from "./index.ts";

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
