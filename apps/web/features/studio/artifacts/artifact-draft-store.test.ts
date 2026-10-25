import assert from "node:assert/strict";
import test from "node:test";
import { artifactDraftStorageKey, createArtifactDraftStore, type ArtifactDraftStorage } from "./artifact-draft-store.ts";

const scope = { workspaceId: "ws_1", threadId: "thr_1", artifactId: "art_1" };
const input = { baseVersion: 2, baseTitle: "Original", baseContent: "Saved text", title: "Draft", content: "Unsaved text" };
function memoryStorage() {
  const values = new Map<string, string>();
  return { values, getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
}

test("writes synchronously to memory and restores after an explicit tab-storage flush", () => {
  const storage = memoryStorage();
  const store = createArtifactDraftStore(() => storage);
  const written = store.write(scope, input);
  assert.equal(written.status, "pending");
  assert.equal(storage.values.size, 0);
  assert.equal(store.read(scope).draft?.content, input.content);
  written.draft!.content = "caller mutation";
  assert.equal(store.flush(scope).status, "persisted");
  const restored = createArtifactDraftStore(() => storage).read(scope);
  assert.equal(restored.status, "persisted");
  assert.equal(restored.draft?.content, input.content);
  assert.equal(restored.draft?.baseContent, input.baseContent);
  assert.equal(restored.draft?.token, written.draft?.token);
});

test("workspace, thread and artifact keys are independently isolated without delimiter collisions", () => {
  const store = createArtifactDraftStore(() => memoryStorage());
  store.write(scope, input);
  for (const other of [{ ...scope, workspaceId: "ws_2" }, { ...scope, threadId: "thr_2" }, { ...scope, artifactId: "art_2" }]) {
    assert.equal(store.read(other).draft, null);
  }
  assert.notEqual(artifactDraftStorageKey({ workspaceId: "a:b", threadId: "c", artifactId: "d" }), artifactDraftStorageKey({ workspaceId: "a", threadId: "b:c", artifactId: "d" }));
});

test("late save deletion and delayed flush cannot erase or persist an obsolete revision", () => {
  const storage = memoryStorage();
  const store = createArtifactDraftStore(() => storage);
  const first = store.write(scope, input).draft!;
  const second = store.write(scope, { ...input, content: "new typing" }).draft!;
  assert.equal(second.revision, first.revision + 1);
  assert.notEqual(first.token, second.token);
  assert.equal(store.remove(scope, first.token).removed, false);
  store.flush(scope, first.token);
  assert.equal(storage.values.size, 0);
  assert.equal(store.flush(scope, second.token).status, "persisted");
  assert.equal(store.remove(scope, second.token).removed, true);
  assert.equal(createArtifactDraftStore(() => storage).read(scope).status, "missing");
});

test("quota and access failures retain every draft in memory and allow explicit retry", () => {
  const backing = memoryStorage();
  let blocked = true;
  const storage: ArtifactDraftStorage = { ...backing, setItem: (key, value) => {
    if (blocked) throw new Error("quota with sensitive provider detail");
    backing.setItem(key, value);
  } };
  const store = createArtifactDraftStore(() => storage);
  store.write(scope, input);
  store.write({ ...scope, artifactId: "art_2" }, { ...input, content: "second draft" });
  assert.ok(store.flushAll().every((item) => item.status === "memory-only"));
  assert.equal(store.read(scope).draft?.content, input.content);
  assert.doesNotMatch(store.read(scope).error!, /sensitive/);
  blocked = false;
  assert.ok(store.flushAll().every((item) => item.status === "persisted"));
  assert.equal(backing.values.size, 2);
  const denied = createArtifactDraftStore(() => { throw new Error("SecurityError"); });
  denied.write(scope, input);
  assert.equal(denied.flush(scope).status, "memory-only");
  assert.equal(denied.read(scope).draft?.content, input.content);
});

test("failed explicit deletion leaves a retryable tombstone without reviving stale saved bytes", () => {
  const backing = memoryStorage();
  let blocked = true;
  const store = createArtifactDraftStore(() => ({ ...backing, removeItem: (key) => {
    if (blocked) throw new Error("denied");
    backing.removeItem(key);
  } }));
  const draft = store.write(scope, input).draft!;
  store.flush(scope);
  assert.deepEqual(store.remove(scope, draft.token), { removed: true, draft: null, status: "memory-only", error: "Draft changes are retained only in this page" });
  assert.equal(store.read(scope).draft, null);
  blocked = false;
  store.flushAll();
  assert.equal(backing.values.size, 0);
});

test("rejects corrupt, foreign and oversized stored snapshots without deleting them", () => {
  const backing = memoryStorage();
  const source = createArtifactDraftStore(() => backing);
  const valid = source.write(scope, input).draft!;
  for (const bad of ["{", JSON.stringify({ ...valid, scope: { ...scope, threadId: "foreign" } }),
    JSON.stringify({ ...valid, baseVersion: 0 }), JSON.stringify({ ...valid, revision: 1.5 }),
    JSON.stringify({ ...valid, baseContent: "a".repeat(500_001) }), JSON.stringify({ ...valid, content: 5 }),
    JSON.stringify({ ...valid, title: "a".repeat(201) }), JSON.stringify({ ...valid, credential: "secret" })]) {
    backing.setItem(artifactDraftStorageKey(scope), bad);
    assert.equal(createArtifactDraftStore(() => backing).read(scope).status, "invalid");
    assert.equal(backing.getItem(artifactDraftStorageKey(scope)), bad);
  }
});

test("enforces edit limits before replacing the recoverable memory draft and strips extra fields", () => {
  const backing = memoryStorage();
  const store = createArtifactDraftStore(() => backing);
  const first = store.write(scope, input).draft!;
  assert.equal(store.write(scope, { ...input, content: "a".repeat(500_001) }).status, "invalid");
  assert.equal(store.read(scope).draft?.token, first.token);
  assert.equal(store.write(scope, { ...input, baseVersion: Number.MAX_SAFE_INTEGER + 1 }).status, "invalid");
  const withExtra = { ...input, content: "a".repeat(500_000), credentials: "must not persist" };
  assert.equal(store.write(scope, withExtra).status, "pending");
  store.flushAll();
  assert.doesNotMatch(backing.getItem(artifactDraftStorageKey(scope))!, /credentials|must not persist/);
  assert.throws(() => store.read({ ...scope, artifactId: "" }), /Invalid/);
});
