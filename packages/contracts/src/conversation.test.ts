import type {
  ContextAssembly,
  MessagePart,
  ThreadDetail,
  ThreadMessage,
} from "./index";

const parts = [
  { type: "text", text: "Review the attached report." },
  {
    type: "attachment",
    attachment_id: "att_report",
    name: "report.pdf",
    media_type: "application/pdf",
  },
] satisfies MessagePart[];

const message = {
  id: "msg_user_1",
  workspace_id: "ws_test",
  thread_id: "thr_test",
  run_id: "run_test",
  agent_version_id: "agv_test",
  sequence: 1,
  role: "user",
  status: "completed",
  parts,
  created_at: "2026-08-29T00:00:00Z",
  completed_at: "2026-08-29T00:00:00Z",
} satisfies ThreadMessage;

const detail = {
  thread: {
    id: "thr_test",
    workspace_id: "ws_test",
    agent_id: "agt_test",
    title: "Contract test",
    context: {},
    context_revision: 1,
    last_message_sequence: 1,
    active_compaction_id: null,
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:00:00Z",
  },
  messages: [message],
  runs: [],
} satisfies ThreadDetail;

const assembly = {
  id: "ctx_test",
  workspace_id: "ws_test",
  thread_id: "thr_test",
  run_id: "run_test",
  agent_version_id: "agv_test",
  entries: [{ kind: "agent_instructions", label: "Agent instructions", included: true }],
  estimated_input_tokens: 128,
  effective_budget_tokens: 8_192,
  message_sequence_through: 1,
  active_compaction_id: null,
  created_at: "2026-08-29T00:00:00Z",
} satisfies ContextAssembly;

void detail;
void assembly;
