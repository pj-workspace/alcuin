import type {
  Agent,
  AgentDefinition,
  AgentVersion,
  CreateAgentPayload,
  CreateAgentVersionPayload,
  CreateThreadPayload,
  Thread,
} from "./index";

const definition = {
  schema_version: "2026-08-28",
  identity: { name: "Versioned Agent", description: "Pinned contracts", icon: "spark" },
  instructions: "Keep every Thread pinned to one immutable Agent version.",
  model: { provider: "test", model: "test-model", credential_ref: null },
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
} satisfies AgentDefinition;

const version = {
  id: "agv_test_v1",
  workspace_id: "ws_test",
  agent_id: "agt_test",
  version: 1,
  definition,
  definition_sha256: "a".repeat(64),
  created_at: "2026-08-29T00:00:00Z",
  published_at: "2026-08-29T00:01:00Z",
} satisfies AgentVersion;

const agent = {
  id: "agt_test",
  workspace_id: "ws_test",
  slug: "versioned-agent",
  name: "Versioned Agent",
  description: "Pinned contracts",
  status: "published",
  current_version_id: "agv_test_v2",
  published_version_id: version.id,
  version: 2,
  definition,
  created_at: "2026-08-29T00:00:00Z",
  updated_at: "2026-08-29T00:01:00Z",
} satisfies Agent;

const thread = {
  id: "thr_test",
  workspace_id: "ws_test",
  agent_id: agent.id,
  agent_version_id: version.id,
  title: "Pinned Thread",
  context: {},
  created_at: "2026-08-29T00:02:00Z",
} satisfies Thread;

const createAgent = { slug: agent.slug, definition } satisfies CreateAgentPayload;
const createVersion = { definition } satisfies CreateAgentVersionPayload;
const createThread = {
  agent_id: agent.id,
  agent_version_id: version.id,
  title: thread.title,
  context: {},
} satisfies CreateThreadPayload;

void createAgent;
void createVersion;
void createThread;
