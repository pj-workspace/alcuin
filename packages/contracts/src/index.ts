export type RunStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "completed"
  | "failed"
  | "cancelled";

export type ExecutionEventType =
  | "run.started"
  | "reasoning.delta"
  | "message.delta"
  | "tool.requested"
  | "tool.completed"
  | "approval.required"
  | "artifact.updated"
  | "citation.created"
  | "run.completed"
  | "run.failed";

export interface AgentDefinition {
  schema_version: string;
  identity: { name: string; description: string; icon: string };
  instructions: string;
  model: { provider: string; model: string; credential_ref?: string | null };
  extensions: string[];
  tools: string[];
  knowledge: string[];
  runtime: { adapter: string; max_steps: number };
  policies: { mutating_tools: "ask" | "deny" | "auto"; external_side_effects: "ask" | "deny" | "auto" };
  context_policy: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  starter_prompts: string[];
}

export interface Agent {
  id: string;
  workspace_id: string;
  slug: string;
  name: string;
  description: string;
  status: "draft" | "published";
  current_version_id: string;
  version: number;
  definition: AgentDefinition;
  created_at: string;
}

export interface ExtensionManifest {
  manifest_version: "1";
  id: string;
  name: string;
  version: string;
  description: string;
  compatibility: string;
  contributions: {
    tools: Array<Record<string, unknown>>;
    skills: Array<Record<string, unknown>>;
    agent_templates: Array<Record<string, unknown>>;
    knowledge_connectors: Array<Record<string, unknown>>;
    ui_blocks: Array<Record<string, unknown>>;
  };
  entrypoints: Array<Record<string, unknown>>;
  config_schema: Record<string, unknown>;
  permissions: Array<{ id: string; reason: string; risk: "low" | "medium" | "high"; required: boolean }>;
  credential_requirements: Array<Record<string, unknown>>;
}

export interface Extension {
  id: string;
  workspace_id: string;
  manifest_id: string;
  name: string;
  version: string;
  status: "enabled" | "disabled";
  health: "healthy" | "degraded" | "unhealthy" | "unchecked";
  manifest: ExtensionManifest;
  credential_refs: Record<string, string>;
  installed_at: string;
}

export interface ExtensionInspection {
  valid: boolean;
  manifest: ExtensionManifest;
  permission_summary: { total: number; high_risk: string[]; requires_review: boolean };
  entrypoints: Array<Record<string, unknown>>;
  install_state: string;
}

export interface KnowledgeSource {
  id: string;
  workspace_id: string;
  name: string;
  description: string;
  status: "ready" | "degraded" | "disabled";
  document_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocument {
  id: string;
  workspace_id: string;
  source_id: string;
  title: string;
  source_uri?: string | null;
  content_hash: string;
  chunk_count: number;
  status: "indexing" | "ready" | "failed";
  metadata: Record<string, unknown>;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Thread {
  id: string;
  workspace_id: string;
  agent_id: string;
  title: string;
  context: Record<string, unknown>;
  created_at: string;
}

export interface Run {
  id: string;
  workspace_id: string;
  thread_id: string;
  agent_version_id: string;
  status: RunStatus;
  input: string;
  title?: string;
  agent_name?: string;
  created_at: string;
  completed_at?: string | null;
}

export interface ImageAttachment {
  type: "image";
  name: string;
  media_type: "image/png" | "image/jpeg" | "image/webp" | "image/gif";
  data_url: string;
}

export interface ExecutionEvent {
  id: string;
  run_id: string;
  sequence: number;
  type: ExecutionEventType;
  timestamp: string;
  payload: Record<string, any>;
}

export interface Artifact {
  id: string;
  title: string;
  kind: string;
  version: number;
  content: string;
}

export interface BootstrapPayload {
  workspace: { id: string; name: string; created_at: string };
  agents: Agent[];
  extensions: Extension[];
  knowledge_sources: KnowledgeSource[];
  threads: Thread[];
  runs: Run[];
}
