export interface ArtifactDraftScope { workspaceId: string; threadId: string; artifactId: string }
export interface ArtifactDraftInput {
  baseVersion: number;
  baseTitle: string;
  baseContent: string;
  title: string;
  content: string;
}
export interface ArtifactDraft extends ArtifactDraftInput {
  scope: ArtifactDraftScope;
  token: string;
  revision: number;
  updatedAt: number;
}
export type ArtifactDraftStatus = "persisted" | "pending" | "memory-only" | "missing" | "invalid";
export interface ArtifactDraftResult { draft: ArtifactDraft | null; status: ArtifactDraftStatus; error?: string }
export interface ArtifactDraftStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
type Entry = ArtifactDraftResult & { scope: ArtifactDraftScope; deleting?: boolean };
const PREFIX = "alcuin:artifact-draft:v1:";
const MAX_CONTENT = 500_000;
const fields = ["baseVersion", "baseTitle", "baseContent", "title", "content", "scope", "token", "revision", "updatedAt"];

function object(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function validScope(value: unknown): value is ArtifactDraftScope {
  return object(value) && Object.keys(value).length === 3 && ["workspaceId", "threadId", "artifactId"].every((key) =>
    typeof value[key] === "string" && value[key].length > 0 && value[key].length <= 512 && !/[\u0000-\u001f]/.test(value[key]));
}
function validInput(value: unknown): value is ArtifactDraftInput {
  return object(value) && Number.isSafeInteger(value.baseVersion) && Number(value.baseVersion) >= 1
    && ["baseTitle", "title"].every((key) => typeof value[key] === "string" && value[key].length <= 200)
    && ["baseContent", "content"].every((key) => typeof value[key] === "string" && value[key].length <= MAX_CONTENT);
}
export function artifactDraftStorageKey(scope: ArtifactDraftScope): string {
  if (!validScope(scope)) throw new Error("Invalid artifact draft scope");
  return PREFIX + JSON.stringify([scope.workspaceId, scope.threadId, scope.artifactId]);
}
function copy(draft: ArtifactDraft | null): ArtifactDraft | null { return draft ? { ...draft, scope: { ...draft.scope } } : null; }
function result(entry: ArtifactDraftResult): ArtifactDraftResult { return { draft: copy(entry.draft), status: entry.status, ...(entry.error ? { error: entry.error } : {}) }; }
function decode(raw: string, scope: ArtifactDraftScope): ArtifactDraft | null {
  if (raw.length > 6_010_000) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!object(value) || Object.keys(value).length !== fields.length || !fields.every((key) => key in value)
      || !validInput(value) || !validScope(value.scope)
      || artifactDraftStorageKey(value.scope) !== artifactDraftStorageKey(scope)
      || typeof value.token !== "string" || value.token.length < 1 || value.token.length > 160
      || !Number.isSafeInteger(value.revision) || Number(value.revision) < 1
      || !Number.isSafeInteger(value.updatedAt) || Number(value.updatedAt) < 0) return null;
    return value as unknown as ArtifactDraft;
  } catch { return null; }
}

/** Synchronous tab-local cache. Call flush after debounce and flushAll on pagehide. */
export function createArtifactDraftStore(storage: () => ArtifactDraftStorage | null = () =>
  typeof window === "undefined" ? null : window.sessionStorage) {
  const entries = new Map<string, Entry>();
  const read = (scope: ArtifactDraftScope): ArtifactDraftResult => {
    const key = artifactDraftStorageKey(scope);
    const existing = entries.get(key);
    if (existing) return result(existing);
    let entry: Entry;
    try {
      const target = storage();
      if (!target) throw new Error("Storage unavailable");
      const raw = target.getItem(key);
      const draft = raw === null ? null : decode(raw, scope);
      entry = { scope: { ...scope }, draft, status: raw === null ? "missing" : draft ? "persisted" : "invalid",
        ...(raw !== null && !draft ? { error: "Saved draft could not be validated" } : {}) };
    } catch {
      entry = { scope: { ...scope }, draft: null, status: "memory-only", error: "Tab storage is unavailable" };
    }
    entries.set(key, entry);
    return result(entry);
  };
  const write = (scope: ArtifactDraftScope, input: ArtifactDraftInput): ArtifactDraftResult => {
    const key = artifactDraftStorageKey(scope);
    const previous = read(scope);
    if (!validInput(input) || previous.draft?.revision === Number.MAX_SAFE_INTEGER) return { ...previous, status: "invalid", error: "Draft exceeds supported limits or has invalid fields" };
    const draft: ArtifactDraft = {
      scope: { ...scope }, baseVersion: input.baseVersion, baseTitle: input.baseTitle, baseContent: input.baseContent,
      title: input.title, content: input.content, token: crypto.randomUUID(),
      revision: (previous.draft?.revision ?? 0) + 1, updatedAt: Date.now(),
    };
    const entry: Entry = { scope: { ...scope }, draft, status: "pending" };
    entries.set(key, entry);
    return result(entry);
  };
  const flush = (scope: ArtifactDraftScope, expectedToken?: string): ArtifactDraftResult => {
    const key = artifactDraftStorageKey(scope);
    read(scope);
    const entry = entries.get(key)!;
    if (expectedToken !== undefined && entry.draft?.token !== expectedToken) return result(entry);
    if (!entry.draft && !entry.deleting) return result(entry);
    try {
      const target = storage();
      if (!target) throw new Error("Storage unavailable");
      if (entry.deleting) target.removeItem(key);
      else target.setItem(key, JSON.stringify(entry.draft));
      entry.status = entry.deleting ? "missing" : "persisted";
      entry.deleting = false;
      delete entry.error;
    } catch {
      entry.status = "memory-only";
      entry.error = "Draft changes are retained only in this page";
    }
    return result(entry);
  };
  const remove = (scope: ArtifactDraftScope, expectedToken: string): ArtifactDraftResult & { removed: boolean } => {
    const key = artifactDraftStorageKey(scope);
    const existing = read(scope);
    if (!existing.draft || existing.draft.token !== expectedToken) return { ...existing, removed: false };
    // Keep a tombstone if persistent deletion fails, so this page cannot revive it.
    entries.set(key, { scope: { ...scope }, draft: null, status: "pending", deleting: true });
    return { ...flush(scope), removed: true };
  };
  const flushAll = (): ArtifactDraftResult[] => [...entries.values()]
    .filter((entry) => entry.draft !== null || entry.deleting)
    .map((entry) => flush(entry.scope));
  return { read, write, flush, flushAll, remove };
}

export const artifactDraftStore = createArtifactDraftStore();
