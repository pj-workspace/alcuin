import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent } from "@alcuin/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";

import { collectCitationSources, remarkCitationMarkers, resolveCitationSource, safeSourceUrl, type CitationMarkdownNode } from "./citation-model.ts";

function citation(sequence: number, payload: Record<string, unknown>): ExecutionEvent {
  return { id: `event-${sequence}`, type: "citation.created", run_id: "run-sources", sequence, timestamp: "2026-09-08T08:00:00Z", payload } as ExecutionEvent;
}

test("sources replay in persisted order with stable numbers, URL deduplication and explicit ID aliases", () => {
  const first = citation(3, { citation_id: "s1", label: "Official source", locator: "https://EXAMPLE.com/report#section", snippet: "The actual source text." });
  const duplicate = citation(8, { citation_id: "s3", label: "Duplicate search result", locator: "https://example.com/report#other" });
  const second = citation(5, { citation_id: "s2", label: "Another report", locator: "https://example.com/report?edition=2" });
  const sources = collectCitationSources([duplicate, second, first, first]);
  assert.equal(sources.length, 2);
  assert.deepEqual(sources.map((source) => source.number), [1, 2]);
  assert.deepEqual(sources[0].ids, ["s1", "s3"]);
  assert.equal(sources[0].snippet, "The actual source text.");
  assert.equal(resolveCitationSource("https://example.com/report#different", sources), sources[0]);
  assert.equal(resolveCitationSource("#alcuin-citation-s3", sources), sources[0]);
  assert.equal(resolveCitationSource("alcuin-citation:s2", sources), sources[1]);
});

test("distinct knowledge passages retain separate evidence and their original chunk location", () => {
  const sources = collectCitationSources([0, 1].map((chunk) => citation(chunk + 1, {
    citation_id: `s${chunk + 1}`,
    locator: `knowledge://knowledge-a/document-a#chunk-${chunk}`,
    label: "Handbook",
    snippet: `Passage ${chunk}`,
    metadata: { kind: "knowledge", source_id: "knowledge-a", document_id: "document-a", chunk_index: chunk, page_number: chunk + 3, source_uri: "https://example.com/handbook.pdf" },
  })));
  assert.equal(sources.length, 2);
  assert.equal(sources[0].chunkIndex, 0);
  assert.equal(sources[0].pageNumber, 3);
  assert.equal(sources[0].url, "https://example.com/handbook.pdf");
  assert.equal(resolveCitationSource("knowledge://knowledge-a/document-a#chunk-1", sources), sources[1]);
  assert.equal(resolveCitationSource("knowledge://knowledge-a/document-a#chunk-9", sources), null);
});

test("old source events remain browsable but never manufacture model references", () => {
  const sources = collectCitationSources([citation(1, { locator: "https://example.com", label: "Legacy citation" })]);
  assert.equal(sources.length, 1);
  assert.equal(resolveCitationSource("#alcuin-citation-s1", sources), null);
  assert.equal(resolveCitationSource("#1", sources), null);
  assert.equal(resolveCitationSource("https://unretrieved.example.com", sources), null);
  assert.equal(resolveCitationSource("https://example.com", sources), sources[0]);
});

test("duplicate IDs on different sources are ambiguous, not confidently assigned", () => {
  const sources = collectCitationSources([
    citation(1, { citation_id: "s1", locator: "https://example.com/a" }),
    citation(2, { citation_id: "s1", locator: "https://example.com/b" }),
  ]);
  assert.equal(resolveCitationSource("alcuin-citation:s1", sources), null);
  assert.equal(resolveCitationSource("https://example.com/a", sources), sources[0]);
});

test("unsafe URLs and credential-bearing links are never exposed as source navigation", () => {
  for (const value of ["javascript:alert(1)", "data:text/html,test", "file:///etc/passwd", "https://user:secret@example.com/path", "/relative", {}, null]) assert.equal(safeSourceUrl(value), null);
  assert.equal(safeSourceUrl("https://example.com/path"), "https://example.com/path");
  const [source] = collectCitationSources([citation(1, { locator: "knowledge://a/b#chunk-0", metadata: { source_uri: "javascript:alert(1)" } })]);
  assert.equal(source.url, null);
});

test("marker parsing supports streaming text and formatted paragraphs without modifying code or existing links", () => {
  const tree: CitationMarkdownNode = { type: "root", children: [
    { type: "paragraph", children: [
      { type: "text", value: "Confirmed [[cite:s1]] and [[cite:s2]]. Partial [[cite:s" },
      { type: "inlineCode", value: "[[cite:s3]]" },
      { type: "strong", children: [{ type: "text", value: "Bold [[cite:s4]]" }] },
      { type: "link", url: "https://example.com", children: [{ type: "text", value: "[[cite:s5]]" }] },
    ] },
    { type: "code", value: "[[cite:s6]]" },
  ] };
  remarkCitationMarkers()(tree);
  const paragraph = tree.children![0].children!;
  assert.equal(paragraph[1].url, "#alcuin-citation-s1");
  assert.equal(paragraph[3].url, "#alcuin-citation-s2");
  assert.equal(paragraph[4].value, ". Partial [[cite:s");
  assert.equal(paragraph[5].value, "[[cite:s3]]");
  assert.equal(paragraph[6].children![1].url, "#alcuin-citation-s4");
  assert.equal(paragraph[7].children![0].value, "[[cite:s5]]");
  assert.equal(tree.children![1].value, "[[cite:s6]]");
});

test("ordinary bracketed numbers and malicious pseudo-IDs stay literal", () => {
  const tree: CitationMarkdownNode = { type: "paragraph", children: [{ type: "text", value: "[1] (K1) [[cite:../secret]] [[cite:<script>]]" }] };
  remarkCitationMarkers()(tree);
  assert.equal(tree.children!.length, 1);
  assert.equal(tree.children![0].type, "text");
});

test("the actual Markdown renderer turns claim markers into source links but leaves sample code literal", () => {
  const html = renderToStaticMarkup(createElement(ReactMarkdown, {
    remarkPlugins: [remarkCitationMarkers],
  }, "The claim **has support** [[cite:s1]].\n\nExample: `[[cite:s2]]`\n\n```text\n[[cite:s3]]\n```"));
  assert.match(html, /<strong>has support<\/strong> <a href="#alcuin-citation-s1">s1<\/a>/);
  assert.match(html, /<code>\[\[cite:s2\]\]<\/code>/);
  assert.match(html, /<pre><code class="language-text">\[\[cite:s3\]\]/);
  assert.doesNotMatch(html, /href="#alcuin-citation-s[23]"/);
});
