import type { ExecutionEvent } from "@alcuin/contracts";

/** Bounded, short-lived cache. Authorization scope is part of identity, never only Run ID. */
export function createRunCitationCache({ maxEntries = 64, ttlMs = 120_000, now = Date.now } = {}) {
  const entries = new Map<string, { expires: number; result: Promise<ExecutionEvent[]> }>();
  return {
    load(scope: string, runId: string, fetchSources: () => Promise<ExecutionEvent[]>): Promise<ExecutionEvent[]> {
      const key = JSON.stringify([scope, runId]);
      const cached = entries.get(key);
      if (cached && cached.expires > now()) return cached.result;
      const entry = {
        expires: now() + ttlMs,
        result: Promise.resolve().then(fetchSources).then((events) => events
          .filter((event) => event.type === "citation.created" && event.run_id === runId)
          .slice(0, 128)),
      };
      entries.delete(key);
      entries.set(key, entry);
      while (entries.size > maxEntries) entries.delete(entries.keys().next().value!);
      entry.result.catch(() => { if (entries.get(key) === entry) entries.delete(key); });
      return entry.result;
    },
  };
}

export function hasCitationReferences(content: string): boolean {
  const prose = content.replace(/```[^\n]*\n[\s\S]*?(?:```|$)|~~~[^\n]*\n[\s\S]*?(?:~~~|$)|`[^`]*`/g, "");
  return /\[\[cite:[a-zA-Z0-9_-]{1,128}\]\]|\]\((?:https?:\/\/|knowledge:\/\/|alcuin-citation:)/.test(prose);
}
