import assert from "node:assert/strict";
import test from "node:test";

import type { Rule, Skill } from "@alcuin/contracts";

import { isManualRuleAvailable, isSkillManuallySelectable } from "./context-ledger-model.ts";

function skill(overrides: Partial<Skill["definition"]> = {}): Skill {
  return {
    id: "skill_1",
    workspace_id: "ws_1",
    slug: "research",
    current_version_id: "skillv_1",
    enabled: true,
    source_kind: "native",
    source_ref: null,
    version: 1,
    definition_sha256: "skill-digest",
    definition: {
      name: "research",
      description: "Research",
      instructions: "Research carefully.",
      disable_model_invocation: false,
      user_invocable: true,
      required_tools: [],
      paths: [],
      metadata: {},
      resources: [],
      ...overrides,
    },
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:00:00Z",
    version_created_at: "2026-08-29T00:00:00Z",
  };
}

function manualRule(scope: Rule["scope"], threadId: string | null = null): Rule {
  return {
    id: "rule_1",
    workspace_id: "ws_1",
    slug: "review",
    current_version_id: "rulev_1",
    scope,
    thread_id: threadId,
    enabled: true,
    source_kind: "native",
    source_ref: null,
    version: 1,
    definition_sha256: "rule-digest",
    definition: {
      name: "review",
      description: "Review before acting",
      content: "Review before acting.",
      activation: "manual",
      conditions: { prompt_terms: [], context_paths: [], file_globs: [] },
      priority: 100,
    },
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:00:00Z",
    version_created_at: "2026-08-29T00:00:00Z",
  };
}

test("only user-invocable, non-always Skills can be selected manually", () => {
  assert.equal(isSkillManuallySelectable(skill(), new Set()), true);
  assert.equal(isSkillManuallySelectable(skill({ user_invocable: false }), new Set()), false);
  assert.equal(isSkillManuallySelectable(skill(), new Set(["skillv_1"])), false);
});

test("Workspace Manual Rules are available without an Agent binding", () => {
  assert.equal(isManualRuleAvailable(manualRule("workspace"), new Set(), "thr_1"), true);
  assert.equal(isManualRuleAvailable(manualRule("library"), new Set(), "thr_1"), false);
  assert.equal(isManualRuleAvailable(manualRule("library"), new Set(["rulev_1"]), "thr_1"), true);
});
