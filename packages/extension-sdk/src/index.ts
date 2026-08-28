import type {
  ExtensionEntrypoint,
  ExtensionManifest,
  ExtensionPermission,
  ExtensionToolContribution,
} from "@alcuin/contracts";

export type {
  ExtensionCredentialRequirement,
  ExtensionEntrypoint,
  ExtensionManifest,
  ExtensionPermission,
  ExtensionToolContribution,
  JsonSchema,
} from "@alcuin/contracts";

export interface ManifestIssue {
  path: string;
  code: string;
  message: string;
  severity: "error" | "warning";
}

export interface ManifestValidationResult {
  valid: boolean;
  errors: ManifestIssue[];
  warnings: ManifestIssue[];
}

const MANIFEST_KEYS = new Set([
  "$schema",
  "manifest_version",
  "id",
  "name",
  "version",
  "description",
  "compatibility",
  "contributions",
  "entrypoints",
  "config_schema",
  "permissions",
  "credential_requirements",
]);
const CONTRIBUTION_KEYS = [
  "tools",
  "skills",
  "agent_templates",
  "knowledge_connectors",
  "ui_blocks",
] as const;
const ID_PATTERN = /^[a-z][a-z0-9.-]{2,127}$/;
const VERSION_PATTERN = /^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$/;
export const MCP_PYTHON_SDK_SPECIFIER = "mcp>=1,<2";

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function addIssue(
  issues: ManifestIssue[],
  path: string,
  code: string,
  message: string,
  severity: ManifestIssue["severity"] = "error",
) {
  issues.push({ path, code, message, severity });
}

function validateEntrypoint(
  value: unknown,
  index: number,
  issues: ManifestIssue[],
): value is ExtensionEntrypoint {
  const path = `entrypoints[${index}]`;
  if (!isObject(value)) {
    addIssue(issues, path, "entrypoint.object", "Entrypoint must be an object.");
    return false;
  }
  if (value.type === "builtin") {
    if (typeof value.adapter !== "string" || !value.adapter.trim()) {
      addIssue(issues, `${path}.adapter`, "entrypoint.adapter", "Built-in entrypoint requires an adapter id.");
    }
    addIssue(
      issues,
      path,
      "entrypoint.builtin-trust",
      "Built-in adapters must be registered by the Alcuin host and are not portable to an unmodified host.",
      "warning",
    );
    return true;
  }
  if (value.type === "mcp") {
    if (!["stdio", "sse", "streamable_http"].includes(String(value.transport))) {
      addIssue(issues, `${path}.transport`, "entrypoint.transport", "MCP transport is not supported.");
    } else if (value.transport === "stdio") {
      if (typeof value.command !== "string" || !value.command.trim()) {
        addIssue(issues, `${path}.command`, "entrypoint.command", "stdio MCP requires a command.");
      }
      if (value.args !== undefined && !Array.isArray(value.args)) {
        addIssue(issues, `${path}.args`, "entrypoint.args", "MCP args must be an array of strings.");
      }
    } else if (!isHttpUrl(value.url)) {
      addIssue(issues, `${path}.url`, "entrypoint.url", "Remote MCP requires an HTTP(S) URL.");
    }
    return true;
  }
  if (value.type === "openapi") {
    if (!isHttpUrl(value.spec_url) && !isHttpUrl(value.base_url)) {
      addIssue(
        issues,
        path,
        "entrypoint.openapi-url",
        "OpenAPI entrypoint requires an HTTP(S) spec_url or base_url.",
      );
    }
    if (value.auth !== undefined && !["none", "api_key", "bearer"].includes(String(value.auth))) {
      addIssue(issues, `${path}.auth`, "entrypoint.auth", "OpenAPI auth must be none, api_key, or bearer.");
    }
    return true;
  }
  addIssue(issues, `${path}.type`, "entrypoint.type", "Entrypoint type must be builtin, mcp, or openapi.");
  return false;
}

function validateTools(value: unknown, issues: ManifestIssue[]): ExtensionToolContribution[] {
  if (!Array.isArray(value)) {
    addIssue(issues, "contributions.tools", "tools.array", "Tools must be an array.");
    return [];
  }
  const names = new Set<string>();
  const tools: ExtensionToolContribution[] = [];
  value.forEach((item, index) => {
    const path = `contributions.tools[${index}]`;
    if (!isObject(item)) {
      addIssue(issues, path, "tool.object", "Tool contribution must be an object.");
      return;
    }
    const name = typeof item.name === "string" ? item.name.trim() : "";
    if (!name) addIssue(issues, `${path}.name`, "tool.name", "Tool name is required.");
    if (names.has(name)) addIssue(issues, `${path}.name`, "tool.duplicate", `Duplicate tool name: ${name}.`);
    if (name) names.add(name);
    if (!isObject(item.input_schema) || item.input_schema.type !== "object") {
      addIssue(issues, `${path}.input_schema`, "tool.schema", "Tool input_schema must be a JSON object schema.");
    }
    if (item.mutating === true && item.approval !== undefined && item.approval !== "ask") {
      addIssue(
        issues,
        `${path}.approval`,
        "tool.mutation-approval",
        "Mutating tools must use approval=ask; hosts still enforce their own policy.",
      );
    }
    tools.push(item as unknown as ExtensionToolContribution);
  });
  return tools;
}

function validatePermissions(value: unknown, issues: ManifestIssue[]): ExtensionPermission[] {
  if (!Array.isArray(value)) {
    addIssue(issues, "permissions", "permissions.array", "Permissions must be an array.");
    return [];
  }
  const ids = new Set<string>();
  const permissions: ExtensionPermission[] = [];
  value.forEach((item, index) => {
    const path = `permissions[${index}]`;
    if (!isObject(item)) {
      addIssue(issues, path, "permission.object", "Permission must be an object.");
      return;
    }
    const id = typeof item.id === "string" ? item.id.trim() : "";
    if (!id) addIssue(issues, `${path}.id`, "permission.id", "Permission id is required.");
    if (ids.has(id)) addIssue(issues, `${path}.id`, "permission.duplicate", `Duplicate permission id: ${id}.`);
    if (id) ids.add(id);
    if (typeof item.reason !== "string" || !item.reason.trim()) {
      addIssue(issues, `${path}.reason`, "permission.reason", "Permission reason is required.");
    }
    if (!["low", "medium", "high"].includes(String(item.risk))) {
      addIssue(issues, `${path}.risk`, "permission.risk", "Permission risk must be low, medium, or high.");
    }
    if (typeof item.required !== "boolean") {
      addIssue(issues, `${path}.required`, "permission.required", "Permission required must be boolean.");
    }
    permissions.push(item as unknown as ExtensionPermission);
  });
  return permissions;
}

export function validateExtensionManifest(value: unknown): ManifestValidationResult {
  const issues: ManifestIssue[] = [];
  if (!isObject(value)) {
    addIssue(issues, "$", "manifest.object", "Manifest must be a JSON object.");
    return { valid: false, errors: issues, warnings: [] };
  }
  Object.keys(value).forEach((key) => {
    if (!MANIFEST_KEYS.has(key)) addIssue(issues, key, "manifest.unknown-key", `Unknown top-level property: ${key}.`);
  });
  if (value.manifest_version !== "1") {
    addIssue(issues, "manifest_version", "manifest.version", "manifest_version must equal \"1\".");
  }
  if (typeof value.id !== "string" || !ID_PATTERN.test(value.id)) {
    addIssue(issues, "id", "manifest.id", "id must match ^[a-z][a-z0-9.-]{2,127}$.");
  }
  if (typeof value.name !== "string" || value.name.length < 2 || value.name.length > 100) {
    addIssue(issues, "name", "manifest.name", "name must contain 2 to 100 characters.");
  }
  if (typeof value.version !== "string" || !VERSION_PATTERN.test(value.version)) {
    addIssue(issues, "version", "manifest.semver", "version must be semantic version text such as 0.1.0.");
  }
  if (value.description !== undefined && (typeof value.description !== "string" || value.description.length > 500)) {
    addIssue(issues, "description", "manifest.description", "description must be at most 500 characters.");
  }
  const contributions = isObject(value.contributions) ? value.contributions : null;
  if (!contributions) {
    addIssue(issues, "contributions", "contributions.object", "contributions must be an object.");
  } else {
    CONTRIBUTION_KEYS.forEach((key) => {
      if (!Array.isArray(contributions[key])) {
        addIssue(issues, `contributions.${key}`, "contributions.array", `${key} must be an array.`);
      }
    });
  }
  const tools = validateTools(contributions?.tools, issues);
  if (!Array.isArray(value.entrypoints) || value.entrypoints.length === 0) {
    addIssue(issues, "entrypoints", "entrypoints.required", "At least one entrypoint is required.");
  } else {
    value.entrypoints.forEach((entrypoint, index) => validateEntrypoint(entrypoint, index, issues));
  }
  const permissions = validatePermissions(value.permissions, issues);
  if (tools.some((tool) => tool.mutating) && !permissions.some((permission) => permission.risk === "high")) {
    addIssue(
      issues,
      "permissions",
      "permissions.mutation-risk",
      "A manifest with mutating tools must declare at least one high-risk permission.",
    );
  }
  if (!isObject(value.config_schema) || (value.config_schema.type !== undefined && value.config_schema.type !== "object")) {
    addIssue(issues, "config_schema", "config.schema", "config_schema must be a JSON object schema.");
  }
  if (!Array.isArray(value.credential_requirements)) {
    addIssue(issues, "credential_requirements", "credentials.array", "credential_requirements must be an array.");
  } else {
    const ids = new Set<string>();
    value.credential_requirements.forEach((item, index) => {
      const path = `credential_requirements[${index}]`;
      if (!isObject(item) || typeof item.id !== "string" || !item.id.trim()) {
        addIssue(issues, `${path}.id`, "credential.id", "Credential requirement id is required.");
        return;
      }
      if (ids.has(item.id)) addIssue(issues, `${path}.id`, "credential.duplicate", `Duplicate credential id: ${item.id}.`);
      ids.add(item.id);
      if (typeof item.type !== "string" || !item.type.trim()) {
        addIssue(issues, `${path}.type`, "credential.type", "Credential type is required.");
      }
    });
  }
  const errors = issues.filter((issue) => issue.severity === "error");
  const warnings = issues.filter((issue) => issue.severity === "warning");
  return { valid: errors.length === 0, errors, warnings };
}

export function assertExtensionManifest(value: unknown): asserts value is ExtensionManifest {
  const result = validateExtensionManifest(value);
  if (!result.valid) {
    throw new TypeError(result.errors.map((issue) => `${issue.path}: ${issue.message}`).join("\n"));
  }
}

export function defineExtension<const T extends ExtensionManifest>(manifest: T): T {
  assertExtensionManifest(manifest);
  return manifest;
}

export function extensionToolId(manifestId: string, toolName: string): string {
  if (!ID_PATTERN.test(manifestId)) throw new TypeError("Invalid extension manifest id.");
  if (!toolName.trim()) throw new TypeError("Tool name is required.");
  return `extension.${manifestId}.${toolName}`;
}
