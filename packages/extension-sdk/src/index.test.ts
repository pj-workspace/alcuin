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

console.log("extension-sdk contract tests passed");
