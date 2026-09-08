import assert from "node:assert/strict";
import test from "node:test";

import type { ExecutionEvent } from "@alcuin/contracts";
import type { ProviderStatus } from "@alcuin/sdk";

import { modelsForAgent, runProfileFromEvents, tierLabelKey } from "./run-profile-model.ts";

const providers: ProviderStatus[] = [{
  id: "deepseek",
  configured: true,
  base_url: "https://api.deepseek.com",
  default_model: "deepseek-flash",
  protocol: "openai-compatible",
  input_modalities: ["text", "image"],
  models: [
    { id: "deepseek-pro", label: "Pro", tier: "pro", input_modalities: ["text"], reasoning_efforts: ["low", "medium", "high"] },
    { id: "deepseek-vision", label: "Vision", tier: "vision", input_modalities: ["text", "image"], reasoning_efforts: ["none", "low", "medium", "high"] },
    { id: "deepseek-flash", label: "Flash", tier: "flash", input_modalities: ["text"], reasoning_efforts: ["none", "low"] },
  ],
}];

test("modelsForAgent exposes only the configured Agent provider in profile order", () => {
  assert.deepEqual(
    modelsForAgent(providers, "deepseek").map((model) => model.id),
    ["deepseek-flash", "deepseek-vision", "deepseek-pro"],
  );
  assert.deepEqual(modelsForAgent(providers, "openai"), []);
  assert.equal(tierLabelKey("vision"), "Vision");
  assert.equal(tierLabelKey("custom"), null);
});

test("runProfileFromEvents reads the effective runtime selection safely", () => {
  const events = [{
    id: "evt_started",
    run_id: "run_test",
    sequence: 1,
    type: "run.started",
    timestamp: "2026-08-29T00:00:00Z",
    payload: { model: "deepseek-pro", reasoning_effort: "medium" },
  }] as ExecutionEvent[];

  assert.deepEqual(runProfileFromEvents(events), {
    model: "deepseek-pro",
    reasoningEffort: "medium",
  });
  assert.deepEqual(runProfileFromEvents([]), { model: null, reasoningEffort: null });
});
