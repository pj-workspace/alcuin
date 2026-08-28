import type { ExecutionEventType } from "./platform";

export {
  AGENT_SCHEMA_VERSION,
  EXECUTION_EVENT_TYPES,
  type ExecutionEventType,
} from "./platform";

export type RunStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "completed"
  | "failed"
  | "cancelled";

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

export type JsonSchema = Record<string, unknown>;

export interface ExtensionToolContribution {
  name: string;
  description?: string;
  input_schema: JsonSchema;
  output_schema?: JsonSchema | null;
  mutating?: boolean;
  approval?: "auto" | "ask" | "deny";
  method?: string;
  path?: string;
  parameter_locations?: Record<string, "path" | "query" | "body">;
  timeout_seconds?: number;
  max_calls_per_run?: number;
}

export interface ExtensionPermission {
  id: string;
  reason: string;
  risk: "low" | "medium" | "high";
  required: boolean;
}

export interface ExtensionCredentialRequirement {
  id: string;
  type: "api_key" | "bearer" | "oauth" | string;
  required?: boolean;
  description?: string;
}

export type ExtensionEntrypoint =
  | { type: "builtin"; adapter: string }
  | {
      type: "mcp";
      transport: "stdio" | "sse" | "streamable_http";
      command?: string | null;
      args?: string[];
      cwd?: string | null;
      url?: string | null;
    }
  | {
      type: "openapi";
      spec_url?: string | null;
      base_url?: string | null;
      auth?: "none" | "api_key" | "bearer";
    };

export type ExtensionUIValueFormat = "text" | "number" | "status" | "date" | "json";

export interface ExtensionUIDataSource {
  kind: "context" | "artifact" | "tool_result";
  tool?: string;
  path?: string;
}

export interface ExtensionUICardBlock {
  id: string;
  type: "card";
  title: string;
  description?: string;
  source: ExtensionUIDataSource;
  fields: Array<{ label: string; path: string; format?: ExtensionUIValueFormat }>;
}

export interface ExtensionUITableBlock {
  id: string;
  type: "table";
  title: string;
  description?: string;
  source: ExtensionUIDataSource;
  columns: Array<{ label: string; path: string; format?: ExtensionUIValueFormat }>;
  empty_state?: string;
}

export interface ExtensionUIFormBlock {
  id: string;
  type: "form";
  title: string;
  description?: string;
  fields: Array<{
    name: string;
    label: string;
    input: "text" | "textarea" | "number" | "select";
    required?: boolean;
    placeholder?: string;
    default_path?: string;
    options?: string[];
  }>;
  submit: { tool: string; label: string };
}

export type ExtensionUIBlock =
  | ExtensionUICardBlock
  | ExtensionUITableBlock
  | ExtensionUIFormBlock;

export interface ExtensionManifest {
  $schema?: string;
  manifest_version: "1";
  id: string;
  name: string;
  version: string;
  description: string;
  compatibility: string;
  contributions: {
    tools: ExtensionToolContribution[];
    skills: Array<Record<string, unknown>>;
    agent_templates: Array<Record<string, unknown>>;
    knowledge_connectors: Array<Record<string, unknown>>;
    ui_blocks: ExtensionUIBlock[];
  };
  entrypoints: ExtensionEntrypoint[];
  config_schema: JsonSchema;
  permissions: ExtensionPermission[];
  credential_requirements: ExtensionCredentialRequirement[];
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

export interface ToolCatalogEntry {
  id: string;
  name: string;
  description: string;
  source: "builtin" | "extension";
  extension_manifest_id: string | null;
  extension_name: string | null;
  mutating: boolean;
  available: boolean;
  status: "available" | "disabled" | "unchecked" | "unhealthy" | "adapter_missing";
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

export interface RequestedToolCall {
  name: string;
  arguments: Record<string, unknown>;
  extension_manifest_id: string;
  ui_block_id: string;
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
  tools: ToolCatalogEntry[];
  knowledge_sources: KnowledgeSource[];
  threads: Thread[];
  runs: Run[];
}
