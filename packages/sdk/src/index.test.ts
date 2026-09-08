import assert from "node:assert/strict";
import test from "node:test";

import {
  AlcuinApiError,
  createAlcuinClient,
  executionEventFromCanonicalFrame,
  executionEventFromChatFrame,
  taskEventFromCanonicalFrame,
} from "./index.ts";

test("client creates and controls durable Tasks without exposing revision UI", async () => {
  const seen: Array<{ url: string; method: string; body: unknown }> = [];
  const task = { id: "task_test", revision: 2, status: "running" };
  const fetchMock: typeof fetch = async (input, init) => {
    seen.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return Response.json(task);
  };
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: fetchMock,
  });

  await client.createTask("thr/test", {
    goal: "Prepare an evidence-backed brief",
    steps: [{ title: "Gather evidence" }, { title: "Draft brief" }],
  });
  await client.commandTask("task/test", {
    command: "pause",
    idempotency_key: "pause-1",
    expected_revision: 2,
    expected_status: "running",
  });

  assert.deepEqual(seen, [
    {
      url: "https://agents.example.test/v1/threads/thr%2Ftest/tasks",
      method: "POST",
      body: {
        goal: "Prepare an evidence-backed brief",
        steps: [{ title: "Gather evidence" }, { title: "Draft brief" }],
      },
    },
    {
      url: "https://agents.example.test/v1/tasks/task%2Ftest/commands",
      method: "POST",
      body: {
        command: "pause",
        idempotency_key: "pause-1",
        expected_revision: 2,
        expected_status: "running",
      },
    },
  ]);
});

test("Task SSE frames require the Workspace-scoped canonical envelope", () => {
  assert.deepEqual(taskEventFromCanonicalFrame({
    id: "tevt_1",
    workspace_id: "ws_test",
    task_id: "task_test",
    sequence: 3,
    type: "task.step.completed",
    timestamp: "2026-08-30T00:00:00Z",
    payload: { step_id: "step_1" },
  }), {
    id: "tevt_1",
    workspace_id: "ws_test",
    task_id: "task_test",
    sequence: 3,
    type: "task.step.completed",
    timestamp: "2026-08-30T00:00:00Z",
    payload: { step_id: "step_1" },
  });
  assert.equal(taskEventFromCanonicalFrame({ task_id: "task_test", sequence: 3 }), null);
});

function sseResponse(...frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

test("citation history uses the bounded source-only endpoint with Workspace scope and encoded Run identity", async () => {
  let url = "";
  let workspace = "";
  const client = createAlcuinClient({ baseUrl: "https://agents.example.test", workspaceId: "ws_sources", fetch: async (input, init) => {
    url = String(input);
    workspace = new Headers(init?.headers).get("X-Alcuin-Workspace") ?? "";
    return Response.json([]);
  } });
  assert.deepEqual(await client.getRunCitations("run/sources"), []);
  assert.equal(url, "https://agents.example.test/v1/runs/run%2Fsources/citations");
  assert.equal(workspace, "ws_sources");
});

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

test("client loads provider profiles and scopes model controls to one Run", async () => {
  const seen: Array<{ url: string; method: string; body: unknown }> = [];
  const fetchMock: typeof fetch = async (input, init) => {
    seen.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (String(input).endsWith("/v1/providers")) {
      return Response.json([{
        id: "deepseek",
        configured: true,
        base_url: "https://api.deepseek.com",
        default_model: "deepseek-v4-flash",
        protocol: "openai-compatible",
        input_modalities: ["text", "image"],
        models: [{
          id: "deepseek-v4-pro",
          label: "DeepSeek V4 Pro",
          tier: "pro",
          input_modalities: ["text"],
          reasoning_efforts: ["low", "medium", "high"],
        }],
      }]);
    }
    return Response.json({ id: "run_test" });
  };
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: fetchMock,
  });

  const providers = await client.listProviders();
  await client.createRun("thr_test", "Investigate", [], {
    modelOverride: "deepseek-v4-pro",
    reasoningEffort: "medium",
  });
  await client.createRun("thr_test", "Fast path", [], false);
  await client.createRun("thr_test", "Legacy default");

  assert.equal(providers[0]?.models[0]?.tier, "pro");
  assert.deepEqual(seen, [
    {
      url: "https://agents.example.test/v1/providers",
      method: "GET",
      body: undefined,
    },
    {
      url: "https://agents.example.test/v1/threads/thr_test/runs",
      method: "POST",
      body: {
        input: "Investigate",
        attachment_ids: [],
        model_override: "deepseek-v4-pro",
        reasoning_effort: "medium",
      },
    },
    {
      url: "https://agents.example.test/v1/threads/thr_test/runs",
      method: "POST",
      body: {
        input: "Fast path",
        attachment_ids: [],
        thinking: false,
      },
    },
    {
      url: "https://agents.example.test/v1/threads/thr_test/runs",
      method: "POST",
      body: {
        input: "Legacy default",
        attachment_ids: [],
        thinking: true,
      },
    },
  ]);
});

test("client manages staged Attachment resources without inline data", async () => {
  const seen: Array<{ url: string; method: string; contentType: string | null; body: BodyInit | null | undefined }> = [];
  const attachment = {
    id: "att_test",
    workspace_id: "ws_test",
    kind: "document",
    name: "brief.pdf",
    media_type: "application/pdf",
    size_bytes: 12_345,
    status: "ready",
    document: { format: "pdf", page_count: 3, extracted_chars: 4_200 },
    created_at: "2026-08-29T00:00:00Z",
    expires_at: "2026-08-29T01:00:00Z",
  };
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    seen.push({ url, method: init?.method ?? "GET", contentType: headers.get("Content-Type"), body: init?.body });
    if (url.endsWith("/content")) return new Response("pdf-bytes", { headers: { "Content-Type": "application/pdf" } });
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return Response.json(attachment);
  };
  const client = createAlcuinClient({ baseUrl: "https://agents.example.test", workspaceId: "ws_test", fetch: fetchMock });
  const file = new File(["pdf"], "brief.pdf", { type: "application/pdf" });

  const uploaded = await client.uploadAttachment(file, "11c39e88-b7dc-4a30-a680-59e29aab08a2");
  const metadata = await client.getAttachment("att/test");
  const content = await client.getAttachmentContent("att/test");
  await client.deleteAttachment("att/test");

  assert.equal(uploaded.document?.page_count, 3);
  assert.equal(metadata.id, "att_test");
  assert.equal(await content.text(), "pdf-bytes");
  assert.deepEqual(seen.map((item) => [item.url, item.method]), [
    ["https://agents.example.test/v1/attachments", "POST"],
    ["https://agents.example.test/v1/attachments/att%2Ftest", "GET"],
    ["https://agents.example.test/v1/attachments/att%2Ftest/content", "GET"],
    ["https://agents.example.test/v1/attachments/att%2Ftest", "DELETE"],
  ]);
  const form = seen[0]?.body as FormData;
  assert.equal(seen[0]?.contentType, null, "browser must generate the multipart boundary");
  assert.equal(form.get("upload_id"), "11c39e88-b7dc-4a30-a680-59e29aab08a2");
  assert.equal((form.get("file") as File).name, "brief.pdf");
});

test("Attachment errors preserve the API detail for retry feedback", async () => {
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: async () => Response.json(
      { detail: { code: "invalid_document", message: "The PDF could not be parsed" } },
      { status: 422 },
    ),
  });

  await assert.rejects(
    () => client.uploadAttachment(new File(["pdf"], "brief.pdf", { type: "application/pdf" }), crypto.randomUUID()),
    /The PDF could not be parsed/,
  );
});

test("client lists, opens, and saves editable Artifact resources", async () => {
  const seen: Array<{ url: string; method: string; body: unknown }> = [];
  const artifact = {
    id: "art_test",
    workspace_id: "ws_test",
    thread_id: "thr/test",
    source_run_id: "run_test",
    title: "Risk brief",
    kind: "document",
    content_type: "text/markdown",
    version: 3,
    content: "# Risk brief",
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:01:00Z",
  };
  const fetchMock: typeof fetch = async (input, init) => {
    seen.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (String(input).includes("/threads/")) return Response.json([artifact]);
    return Response.json(artifact);
  };
  const client = createAlcuinClient({ baseUrl: "https://agents.example.test", workspaceId: "ws_test", fetch: fetchMock });

  await client.listArtifacts("thr/test", 20);
  await client.getArtifact("art/test");
  await client.updateArtifact("art/test", { expected_version: 2, title: "Risk brief", content: "# Risk brief" });

  assert.deepEqual(seen, [
    { url: "https://agents.example.test/v1/threads/thr%2Ftest/artifacts?limit=20", method: "GET", body: undefined },
    { url: "https://agents.example.test/v1/artifacts/art%2Ftest", method: "GET", body: undefined },
    { url: "https://agents.example.test/v1/artifacts/art%2Ftest", method: "PATCH", body: { expected_version: 2, title: "Risk brief", content: "# Risk brief" } },
  ]);
});

test("Artifact downloads return bytes, escape ids, and retain Workspace ownership", async () => {
  const seen: Array<{ url: string; workspace: string | null }> = [];
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: async (input, init) => {
      seen.push({ url: String(input), workspace: new Headers(init?.headers).get("X-Alcuin-Workspace") });
      return new Response(new Uint8Array([80, 75, 3, 4]), { headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document" } });
    },
  });
  const document = await client.downloadArtifact("art/test", "docx");
  assert.equal(document.type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  assert.deepEqual([...new Uint8Array(await document.arrayBuffer())], [80, 75, 3, 4]);
  assert.deepEqual(seen, [{ url: "https://agents.example.test/v1/artifacts/art%2Ftest/download?format=docx", workspace: "ws_test" }]);
});

test("Artifact download failures retain structured API errors", async () => {
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test", workspaceId: "ws_other",
    fetch: async () => Response.json({ detail: { code: "artifact_not_found", message: "Artifact not found" } }, { status: 404 }),
  });
  await assert.rejects(() => client.downloadArtifact("art_test", "html"), (error) => error instanceof AlcuinApiError && error.status === 404 && error.message === "Artifact not found");
});

test("Artifact conflicts expose status and structured detail", async () => {
  const client = createAlcuinClient({
    baseUrl: "https://agents.example.test",
    workspaceId: "ws_test",
    fetch: async () => Response.json({
      detail: {
        code: "artifact_version_conflict",
        message: "Artifact changed since it was opened",
        current_version: 4,
      },
    }, { status: 409 }),
  });

  await assert.rejects(
    () => client.updateArtifact("art_test", { expected_version: 3, content: "local edit" }),
    (error) => error instanceof AlcuinApiError
      && error.status === 409
      && (error.detail as { current_version?: number }).current_version === 4,
  );
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

test("customization APIs persist next-turn context and verify plugin bytes", async () => {
  const seen: Array<{ url: string; method: string; body: unknown; contentType: string | null }> = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    seen.push({ url, method: init?.method ?? "GET", body: init?.body, contentType: headers.get("Content-Type") });
    if (url.endsWith("/v1/plugins/inspect")) return Response.json({ inspection_receipt: "alcpir1.signed.review", inspection_digest: "a".repeat(64), permissions_hash: "b".repeat(64), policy_revision: "plugin-install-policy-v1", expires_at: 1_800_000_000, inspection: { format: "cursor-plugin", name: "Example", version: "1.0.0", description: "", manifest: {}, skills: [], rules: [], mcp_servers: [], credential_variables: [], disabled_components: [], permissions: [], warnings: [], install_state: "ready_for_disabled_install" } });
    if (url.endsWith("/v1/plugins/install")) return Response.json({ skills: [], rules: [], plugin: { format: "cursor-plugin", name: "Example", version: "1.0.0" }, status: "installed_disabled", mcp_servers: [], mcp_install_state: "not_declared", disabled_components: [], warnings: [] });
    if (url.endsWith("/configuration")) return Response.json({ thread_id: "thr_test", workspace_id: "ws_test", agent_version_id: "agv_test", revision: 2, active_skill_version_ids: ["skv_test"], manual_rule_version_ids: [], created_at: "", updated_at: "" });
    return Response.json([]);
  };
  const client = createAlcuinClient({ baseUrl: "https://agents.example.test", workspaceId: "ws_test", fetch: fetchMock });

  await client.listSkills();
  await client.listRules();
  await client.updateThreadConfiguration("thr/test", { expected_revision: 1, active_skill_version_ids: ["skv_test"], manual_rule_version_ids: [] });
  const file = new File(["plugin"], "plugin.zip", { type: "application/zip" });
  const inspection = await client.inspectPluginPackage(file);
  await client.installPluginPackage(file, inspection.inspection_receipt);

  assert.deepEqual(seen.map((item) => [item.url, item.method]), [
    ["https://agents.example.test/v1/skills", "GET"],
    ["https://agents.example.test/v1/rules", "GET"],
    ["https://agents.example.test/v1/threads/thr%2Ftest/configuration", "PATCH"],
    ["https://agents.example.test/v1/plugins/inspect", "POST"],
    ["https://agents.example.test/v1/plugins/install", "POST"],
  ]);
  assert.equal(seen[2].body, JSON.stringify({ expected_revision: 1, active_skill_version_ids: ["skv_test"], manual_rule_version_ids: [] }));
  assert.equal(seen[3].contentType, null, "browser must generate the multipart boundary");
  assert.equal(seen[4].contentType, null, "browser must generate the multipart boundary");
  assert.equal((seen[4].body as FormData).get("inspection_receipt"), "alcpir1.signed.review");
  assert.equal((seen[4].body as FormData).get("inspection_digest"), null);
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
    skills: [],
    rules: [],
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
      eventId: "evt_meta",
      runId: "run_1",
      sequence: 1,
      timestamp: "2026-08-28T00:00:00Z",
      type: "meta",
      provider: "deepseek",
      chatModel: "deepseek-v4-pro",
      runtime: "langgraph-react",
      thinking: true,
      reasoningEffort: "high",
      inputModalities: ["text"],
      attachmentCount: 0,
    }),
    {
      id: "evt_meta",
      run_id: "run_1",
      sequence: 1,
      timestamp: "2026-08-28T00:00:00Z",
      type: "run.started",
      payload: {
        provider: "deepseek",
        model: "deepseek-v4-pro",
        runtime: "langgraph-react",
        thinking: true,
        reasoning_effort: "high",
        input_modalities: ["text"],
        attachment_count: 0,
      },
    },
  );
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
