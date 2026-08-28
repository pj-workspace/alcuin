import assert from "node:assert/strict";

import type { Agent, ExecutionEvent, Extension } from "@alcuin/contracts";

import {
  dataForExtensionBlock,
  resolveExtensionUIBlocks,
  valueAtPath,
} from "./extension-ui.ts";

const block = {
  id: "records",
  type: "table" as const,
  title: "Records",
  source: { kind: "tool_result" as const, tool: "search", path: "records" },
  columns: [{ label: "ID", path: "id" }],
};
const agent = {
  definition: {
    extensions: ["acme.records"],
    tools: ["extension.acme.records.search"],
  },
} as Agent;
const extension = {
  id: "ext_records",
  name: "Records",
  status: "enabled",
  health: "healthy",
  manifest: {
    id: "acme.records",
    entrypoints: [{ type: "mcp", transport: "streamable_http", url: "https://example.test/mcp" }],
    contributions: { ui_blocks: [block] },
  },
} as Extension;

const resolved = resolveExtensionUIBlocks(agent, [extension]);
assert.equal(resolved.length, 1);
assert.equal(resolveExtensionUIBlocks(agent, [{ ...extension, status: "disabled" }]).length, 0);
assert.equal(resolveExtensionUIBlocks({
  ...agent,
  definition: { ...agent.definition, tools: [] },
}, [extension]).length, 0);
assert.equal(valueAtPath({ rows: [{ id: "A-1" }] }, "rows.0.id"), "A-1");

const events = [{
  id: "evt_1",
  run_id: "run_1",
  sequence: 1,
  type: "tool.completed",
  timestamp: new Date(0).toISOString(),
  payload: {
    tool: "extension.acme.records.search",
    status: "succeeded",
    result: { records: [{ id: "A-1" }] },
  },
}] satisfies ExecutionEvent[];
assert.deepEqual(dataForExtensionBlock(resolved[0], events, {}), [{ id: "A-1" }]);

console.log("extension UI resolution tests passed");
