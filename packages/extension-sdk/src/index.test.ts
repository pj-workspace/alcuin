import assert from "node:assert/strict";

import {
  MCP_PYTHON_SDK_SPECIFIER,
  defineExtension,
  extensionToolId,
  validateExtensionManifest,
} from "./index.ts";

assert.equal(MCP_PYTHON_SDK_SPECIFIER, "mcp>=1,<2");

const valid = defineExtension({
  manifest_version: "1",
  id: "verification.sdk",
  name: "SDK Verification",
  version: "0.1.0",
  description: "Verify the public SDK contract.",
  compatibility: ">=0.1.0",
  contributions: {
    tools: [
      {
        name: "echo",
        input_schema: { type: "object", properties: {} },
        mutating: false,
      },
    ],
    skills: [],
    agent_templates: [],
    knowledge_connectors: [],
    ui_blocks: [],
  },
  entrypoints: [
    {
      type: "mcp",
      transport: "streamable_http",
      url: "https://mcp.example.test/mcp",
    },
  ],
  config_schema: { type: "object" },
  permissions: [
    { id: "tools:read", reason: "Read through MCP", risk: "low", required: true },
  ],
  credential_requirements: [],
});

assert.equal(valid.id, "verification.sdk");
assert.equal(extensionToolId(valid.id, "echo"), "extension.verification.sdk.echo");

const validUi = validateExtensionManifest({
  ...valid,
  contributions: {
    ...valid.contributions,
    ui_blocks: [
      {
        id: "context-card",
        type: "card",
        title: "Context",
        source: { kind: "context", path: "record" },
        fields: [{ label: "ID", path: "id" }],
      },
      {
        id: "result-table",
        type: "table",
        title: "Results",
        source: { kind: "tool_result", tool: "echo", path: "rows" },
        columns: [{ label: "Value", path: "value" }],
      },
      {
        id: "echo-form",
        type: "form",
        title: "Echo",
        fields: [{ name: "message", label: "Message", input: "text" }],
        submit: { tool: "echo", label: "Send" },
      },
    ],
  },
});
assert.equal(validUi.valid, true);

const invalid = validateExtensionManifest({
  ...valid,
  contributions: {
    ...valid.contributions,
    tools: [
      {
        name: "write",
        input_schema: { type: "object" },
        mutating: true,
        approval: "auto",
      },
    ],
  },
});

assert.equal(invalid.valid, false);
assert.deepEqual(
  invalid.errors.map((issue) => issue.code).sort(),
  ["permissions.mutation-risk", "tool.mutation-approval"],
);

const invalidUi = validateExtensionManifest({
  ...valid,
  contributions: {
    ...valid.contributions,
    ui_blocks: [
      {
        id: "lookup",
        type: "form",
        title: "Lookup",
        fields: [{ name: "query", label: "Query", input: "text" }],
        submit: { tool: "missing", label: "Run" },
      },
    ],
  },
});
assert.equal(invalidUi.valid, false);
assert.equal(invalidUi.errors.some((issue) => issue.code === "ui-block.submit-tool"), true);

console.log("extension-sdk contract tests passed");
