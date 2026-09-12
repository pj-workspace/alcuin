import assert from "node:assert/strict";
import test from "node:test";
import type { Thread } from "@alcuin/contracts";
import { mergeThreadTitle, threadTitleStatus, threadTitleText, threadTitleWatchAction } from "./thread-title-model.ts";

const thread = { id: "thr_1", workspace_id: "ws_1", agent_id: "agt_1", agent_version_id: "agv_1", title: "Working session", title_status: "pending", context: {}, created_at: "2026-09-12T00:00:00Z" } satisfies Thread;

test("legacy placeholders localize while explicit user titles stay intact", () => {
  assert.equal(threadTitleText(thread, "zh"), "新会话");
  assert.equal(threadTitleText({ title: "New agent thread" }, "en"), "New thread");
  assert.equal(threadTitleStatus({ title: "Working session", title_status: "ready" }), "ready");
  assert.equal(threadTitleText({ title: "Working session", title_status: "ready" }, "zh"), "Working session");
  assert.equal(threadTitleText({ ...thread, title_status: "generating" }, "zh"), "正在命名会话…");
});

test("late or cross-scope naming cannot overwrite a newer title or other Thread data", () => {
  const ready: Thread = { ...thread, title: "User-selected title", title_status: "ready", updated_at: "2026-09-12T00:00:03Z", context: { selected: true } };
  assert.equal(mergeThreadTitle(ready, { ...ready, id: "thr_2", title: "Wrong thread" }), ready);
  assert.equal(mergeThreadTitle(ready, { ...ready, workspace_id: "ws_2", title: "Wrong workspace" }), ready);
  assert.equal(mergeThreadTitle(ready, { ...ready, updated_at: "2026-09-12T00:00:02Z", title: "Stale title" }), ready);
  assert.equal(mergeThreadTitle(ready, { ...thread, updated_at: "2026-09-12T00:00:04Z" }), ready);
  const merged = mergeThreadTitle(thread, ready);
  assert.equal(merged.title, ready.title);
  assert.equal(merged.context, thread.context);
  const refreshed = mergeThreadTitle(ready, { ...ready, updated_at: "2026-09-12T00:00:05Z" });
  assert.equal(refreshed.updated_at, "2026-09-12T00:00:05Z");
  assert.equal(mergeThreadTitle(refreshed, { ...ready, title: "Outdated model title", updated_at: "2026-09-12T00:00:04Z" }), refreshed);
});

test("a crashed title lease is reclaimed after 32 seconds with time left to observe completion", () => {
  const watch = { status: "generating" as const, attempts: 24, elapsedMs: 21_450, recovered: false };
  assert.equal(threadTitleWatchAction(watch), "read");
  assert.equal(threadTitleWatchAction({ ...watch, elapsedMs: 31_999 }), "read");
  assert.equal(threadTitleWatchAction({ ...watch, elapsedMs: 32_000 }), "ensure");
  assert.equal(threadTitleWatchAction({ ...watch, elapsedMs: 32_001, recovered: true }), "read");
  assert.equal(threadTitleWatchAction({ ...watch, elapsedMs: 44_000, recovered: true }), "read");
  assert.equal(threadTitleWatchAction({ ...watch, status: "ready", elapsedMs: 44_000, recovered: true }), "stop");
  assert.equal(threadTitleWatchAction({ ...watch, elapsedMs: 54_999, recovered: true }), "read");
  assert.equal(threadTitleWatchAction({ ...watch, elapsedMs: 55_000, recovered: true }), "stop");
  assert.equal(threadTitleWatchAction({ ...watch, status: "pending", attempts: 4, elapsedMs: 1950 }), "stop");
});
