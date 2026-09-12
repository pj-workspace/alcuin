import type { ExecutionEvent } from "@alcuin/contracts";

export interface CitationSource {
  key: string;
  number: number;
  ids: string[];
  title: string;
  publisher: string;
  locator: string;
  url: string | null;
  snippet: string | null;
  kind: "web" | "knowledge" | "other";
  documentId: string | null;
  sourceId: string | null;
  chunkIndex: number | null;
  pageNumber: number | null;
  retrievedAt: string;
}

export function safeSourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return /^(https?:)$/.test(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function canonicalLocator(value: string): string {
  const safe = safeSourceUrl(value);
  if (!safe) return value.trim();
  const url = new URL(safe);
  // Fragments target passages of the same page; query strings may identify different documents.
  url.hash = "";
  return url.href;
}

function nonempty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** IDs belong to this run's persisted source events, never to model-created numbering. */
export function collectCitationSources(events: ExecutionEvent[]): CitationSource[] {
  const sources: CitationSource[] = [];
  const seenEvents = new Set<string>();
  const seenSources = new Map<string, CitationSource>();
  for (const event of events.filter((event) => event.type === "citation.created").sort((left, right) => left.sequence - right.sequence)) {
    if (seenEvents.has(event.id)) continue;
    seenEvents.add(event.id);
    const payload = event.payload as Record<string, unknown>;
    const meta = payload.metadata && typeof payload.metadata === "object"
      ? payload.metadata as Record<string, unknown> : {};
    const locator = nonempty(payload.locator) ?? "";
    const key = locator ? canonicalLocator(locator) : `event:${event.id}`;
    const id = nonempty(payload.citation_id);
    const previous = seenSources.get(key);
    if (previous) {
      if (id && !previous.ids.includes(id)) previous.ids.push(id);
      previous.snippet ||= nonempty(payload.snippet);
      continue;
    }
    const knowledge = meta.kind === "knowledge" || locator.startsWith("knowledge://");
    const url = safeSourceUrl(knowledge ? meta.source_uri : locator);
    const source: CitationSource = {
      key,
      number: sources.length + 1,
      ids: id ? [id] : [],
      title: nonempty(payload.label) ?? nonempty(payload.source) ?? locator,
      publisher: nonempty(payload.source) ?? (url ? new URL(url).hostname : ""),
      locator,
      url,
      snippet: nonempty(payload.snippet),
      kind: knowledge ? "knowledge" : url ? "web" : "other",
      sourceId: nonempty(meta.source_id),
      documentId: nonempty(meta.document_id),
      chunkIndex: integer(meta.chunk_index),
      pageNumber: integer(meta.page_number) ?? integer(meta.page),
      retrievedAt: event.timestamp,
    };
    sources.push(source);
    seenSources.set(key, source);
  }
  return sources;
}

/** Per-view cache for immutable event snapshots; unrelated deltas never change sources. */
export function createCitationSourceSelector() {
  let previous: ExecutionEvent[] = [];
  let sources: CitationSource[] = [];
  return (...batches: readonly ExecutionEvent[][]): CitationSource[] => {
    const citations: ExecutionEvent[] = [];
    for (const events of batches) {
      for (const event of events) if (event.type === "citation.created") citations.push(event);
    }
    if (citations.length === previous.length && citations.every((event, index) => event === previous[index])) return sources;
    previous = citations;
    sources = collectCitationSources(citations);
    return sources;
  };
}

export function citationIdFromHref(href: string): string | null {
  const match = /^(?:alcuin-citation:|#alcuin-citation-)([a-zA-Z0-9_-]{1,128})$/.exec(href);
  return match?.[1] ?? null;
}

export function resolveCitationSource(href: string, sources: CitationSource[]): CitationSource | null {
  const id = citationIdFromHref(href);
  if (id) {
    const matches = sources.filter((source) => source.ids.includes(id));
    // An ambiguous backend ID must never point confidently at the wrong source.
    return matches.length === 1 ? matches[0] : null;
  }
  if (!safeSourceUrl(href) && !href.startsWith("knowledge://")) return null;
  return sources.find((source) => canonicalLocator(source.locator) === canonicalLocator(href)) ?? null;
}

export interface CitationMarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: CitationMarkdownNode[];
}

/** Transform text nodes only: Markdown links, fenced code and inline code stay untouched. */
export function remarkCitationMarkers() {
  return (tree: CitationMarkdownNode) => {
    function walk(node: CitationMarkdownNode) {
      if (!node.children || ["code", "inlineCode", "link", "linkReference", "html"].includes(node.type)) return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) { walk(child); return [child]; }
        const tokens: CitationMarkdownNode[] = [];
        const pattern = /\[\[cite:([a-zA-Z0-9_-]{1,128})\]\]/g;
        let cursor = 0;
        for (const match of child.value.matchAll(pattern)) {
          const index = match.index!;
          if (index > cursor) tokens.push({ type: "text", value: child.value.slice(cursor, index) });
          tokens.push({ type: "link", url: `#alcuin-citation-${match[1]}`, children: [{ type: "text", value: match[1] }] });
          cursor = index + match[0].length;
        }
        if (!tokens.length) return [child];
        if (cursor < child.value.length) tokens.push({ type: "text", value: child.value.slice(cursor) });
        return tokens;
      });
    }
    walk(tree);
  };
}
