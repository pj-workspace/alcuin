import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent } from "@alcuin/contracts";

import { createRunCitationCache, hasCitationReferences } from "./citation-cache.ts";

function source(runId: string): ExecutionEvent {
  return { id: `source-${runId}`, run_id: runId, type: "citation.created", sequence: 1, timestamp: "2026-09-08T00:00:00Z", payload: { citation_id: "s1", locator: "https://example.com" } } as ExecutionEvent;
}

test("on-demand citation cache deduplicates requests but isolates Workspace and API scopes", async () => {
  const cache = createRunCitationCache();
  let calls = 0;
  const loader = async () => { calls++; return [source("run-one")]; };
  const [first, second] = await Promise.all([cache.load("api-a/ws-a", "run-one", loader), cache.load("api-a/ws-a", "run-one", loader)]);
  assert.equal(calls, 1);
  assert.equal(first, second);
  await cache.load("api-a/ws-b", "run-one", loader);
  await cache.load("api-b/ws-a", "run-one", loader);
  assert.equal(calls, 3);
});

test("failed lookups retry and foreign Runs or full trace events are never cached as sources", async () => {
  const cache = createRunCitationCache();
  await assert.rejects(cache.load("scope", "run-one", async () => { throw new Error("offline"); }));
  const result = await cache.load("scope", "run-one", async () => [source("run-one"), source("run-foreign"), { ...source("run-one"), type: "message.delta" } as ExecutionEvent]);
  assert.deepEqual(result, [source("run-one")]);
});

test("cache entries expire and its retained Run count stays bounded", async () => {
  let clock = 1;
  const cache = createRunCitationCache({ maxEntries: 2, ttlMs: 100, now: () => clock });
  let calls = 0;
  const read = (runId: string) => cache.load("scope", runId, async () => { calls++; return [source(runId)]; });
  await read("one"); await read("two"); await read("three");
  await read("one");
  assert.equal(calls, 4);
  clock = 102;
  await read("one");
  assert.equal(calls, 5);
});

test("only source-bearing prose offers historical loading, not every reply or literal code examples", () => {
  assert.equal(hasCitationReferences("A claim [[cite:s1]]."), true);
  assert.equal(hasCitationReferences("A claim [source](https://example.com)."), true);
  assert.equal(hasCitationReferences("A claim [passage](knowledge://a/b#chunk-0)."), true);
  assert.equal(hasCitationReferences("Hello, how can I help?"), false);
  assert.equal(hasCitationReferences("A sample `[[cite:s1]]` is code."), false);
  assert.equal(hasCitationReferences("```markdown\n[[cite:s1]]\n```"), false);
});
