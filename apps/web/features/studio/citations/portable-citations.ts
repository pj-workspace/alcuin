import { fromMarkdown } from "mdast-util-from-markdown";
import type { ExecutionEvent } from "@alcuin/contracts";

import { citationIdFromHref, collectCitationSources, resolveCitationSource, type CitationSource } from "./citation-model.ts";

type MarkdownNode = {
  type: string;
  value?: string;
  identifier?: string;
  url?: string;
  children?: MarkdownNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
};
type Edit = { start: number; end: number; text: string };
type Reference = { start: number; end: number; href: string; label?: string };

function escapeLabel(value: string): string {
  return value.replace(/[\\`*{}\[\]<>_!|]/g, "\\$&").replace(/[\r\n]+/g, " ");
}

function plainText(node: MarkdownNode): string {
  if ("value" in node && typeof node.value === "string") return node.value;
  return node.children?.map(plainText).join("") ?? "";
}

function referencesInMarkdown(content: string): { references: Reference[]; definitions: Edit[] } {
  const tree = fromMarkdown(content);
  const references: Reference[] = [];
  const definitions: Edit[] = [];
  const definitionsById = new Map<string, string>();
  function collectDefinitions(node: MarkdownNode) {
    if (node.type === "definition" && node.identifier && node.url && !definitionsById.has(node.identifier)) definitionsById.set(node.identifier, node.url);
    if (node.children) for (const child of node.children) collectDefinitions(child);
  }
  collectDefinitions(tree);

  function visit(node: MarkdownNode) {
    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    if (start === undefined || end === undefined || ["code", "inlineCode", "html", "image", "imageReference"].includes(node.type)) return;
    if (node.type === "definition") {
      if (node.url && (citationIdFromHref(node.url) || node.url.startsWith("knowledge://"))) definitions.push({ start, end, text: "" });
      return;
    }
    if (node.type === "link" || node.type === "linkReference") {
      const href = node.type === "link" ? node.url : definitionsById.get(node.identifier ?? "");
      if (href && (citationIdFromHref(href) || href.startsWith("knowledge://"))) references.push({ start, end, href, label: plainText(node) });
      return;
    }
    if (node.type === "text") {
      // Source offsets, rather than decoded node text, preserve escapes and all other formatting.
      const raw = content.slice(start, end);
      for (const match of raw.matchAll(/\[\[cite:([a-zA-Z0-9_-]{1,128})\]\]/g)) {
        const offset = match.index!;
        let backslashes = 0;
        for (let index = offset - 1; index >= 0 && raw[index] === "\\"; index--) backslashes++;
        if (backslashes % 2) continue;
        references.push({ start: start + offset, end: start + offset + match[0].length, href: `alcuin-citation:${match[1]}` });
      }
      return;
    }
    if (node.children) for (const child of node.children) visit(child);
  }
  visit(tree);
  return { references, definitions };
}

export function hasUnresolvedPortableCitations(content: string, sources: CitationSource[]): boolean {
  return referencesInMarkdown(content).references.some((reference) => !resolveCitationSource(reference.href, sources));
}

/** Resolve evidence only for the copied Run; an unavailable history is not fabricated. */
export async function resolvePortableCitationMarkdown({ content, events, runId, locale = "en", loadSources }: {
  content: string;
  events: readonly ExecutionEvent[];
  runId?: string | null;
  locale?: "en" | "zh";
  loadSources?: (runId: string) => Promise<ExecutionEvent[]>;
}): Promise<string> {
  const scoped = events.filter((event) => Boolean(runId) && event.run_id === runId);
  let sources = collectCitationSources(scoped);
  if (runId && loadSources && hasUnresolvedPortableCitations(content, sources)) {
    try {
      const history = (await loadSources(runId)).filter((event) => event.run_id === runId);
      sources = collectCitationSources([...scoped, ...history]);
    } catch {
      // Preserve the response while making missing evidence explicit in its portable form.
    }
  }
  return portableCitationMarkdown(content, sources, locale);
}

/** Replace only real citation syntax, preserving authored Markdown and code samples byte-for-byte. */
export function portableCitationMarkdown(content: string, sources: CitationSource[], locale: "en" | "zh" = "en"): string {
  const { references, definitions } = referencesInMarkdown(content);
  if (!references.length && !definitions.length) return content;
  const offlineSources = new Map<string, CitationSource>();
  const edits: Edit[] = [...definitions];
  for (const reference of references) {
    const source = resolveCitationSource(reference.href, sources);
    const label = reference.label && !source?.ids.includes(reference.label) ? escapeLabel(reference.label) : undefined;
    let replacement: string;
    if (!source) {
      replacement = locale === "zh" ? "（引用来源未核验）" : "(unverified source)";
      if (label && !/^s[1-9][0-9]*$/.test(label)) replacement = `${label} ${replacement}`;
    } else if (source.url) {
      replacement = `[${label || source.number}](<${source.url.replace(/</g, "%3C").replace(/>/g, "%3E")}>)`;
    } else {
      const marker = locale === "zh" ? `（来源 ${source.number}）` : `(source ${source.number})`;
      replacement = label ? `${label} ${marker}` : marker;
      offlineSources.set(source.key, source);
    }
    edits.push({ start: reference.start, end: reference.end, text: replacement });
  }
  let result = content;
  for (const edit of edits.sort((left, right) => right.start - left.start)) result = result.slice(0, edit.start) + edit.text + result.slice(edit.end);
  if (offlineSources.size) {
    const notes = [...offlineSources.values()].map((source) => {
      const location = [
        source.kind === "knowledge" ? locale === "zh" ? "知识库" : "Knowledge" : source.publisher,
        source.pageNumber !== null ? `${locale === "zh" ? "页" : "Page"} ${source.pageNumber}` : "",
        source.chunkIndex !== null ? `${locale === "zh" ? "片段" : "Passage"} ${source.chunkIndex + 1}` : "",
      ].filter(Boolean).map(escapeLabel).join(" · ");
      // Opaque/unsafe locators are not useful portable titles and may contain private identifiers.
      const title = escapeLabel(source.title && source.title !== source.locator ? source.title : locale === "zh" ? "未命名来源" : "Untitled source");
      const excerpt = source.snippet ? `\n\n${source.snippet.split(/\r?\n/).map((line) => `> ${escapeLabel(line)}`).join("\n")}` : "";
      return `${locale === "zh" ? "来源" : "Source"} ${source.number}: ${title}${location ? ` — ${location}` : ""}${excerpt}`;
    });
    result = `${result.trimEnd()}\n\n---\n\n${locale === "zh" ? "引用来源（保存的检索摘录）" : "Sources (saved retrieval excerpts)"}\n\n${notes.join("\n\n")}`;
  }
  return result;
}
