import assert from "node:assert/strict";
import test from "node:test";
import type { ExecutionEvent } from "@alcuin/contracts";
import { fromMarkdown } from "mdast-util-from-markdown";

import { collectCitationSources } from "./citation-model.ts";
import { hasUnresolvedPortableCitations, portableCitationMarkdown, resolvePortableCitationMarkdown } from "./portable-citations.ts";

function event(sequence: number, payload: Record<string, unknown>): ExecutionEvent {
  return { id: `citation-${sequence}`, type: "citation.created", run_id: "run-copy", sequence, timestamp: "2026-09-08T08:00:00Z", payload } as ExecutionEvent;
}

const webSources = collectCitationSources([
  event(1, { citation_id: "s1", locator: "https://example.com/reports/annual_(2026)#summary", label: "Annual report" }),
  event(2, { citation_id: "s2", locator: "https://example.com/second", label: "Second source" }),
]);

test("native markers become portable links without rewriting headings, lists, tables or surrounding formatting", () => {
  const input = "# Findings\n\n- **First** [[cite:s1]]\n- Second [[cite:s2]]\n\n| Item | Evidence |\n| --- | --- |\n| A | [[cite:s1]] |";
  const output = portableCitationMarkdown(input, webSources);
  assert.equal(output, input.replaceAll("[[cite:s1]]", "[1](<https://example.com/reports/annual_(2026)#summary>)").replaceAll("[[cite:s2]]", "[2](<https://example.com/second>)"));
  assert.doesNotMatch(output, /\[\[cite:/);
  assert.equal(hasUnresolvedPortableCitations(input, webSources), false);
  const parsed = fromMarkdown(portableCitationMarkdown("[[cite:s1]]", webSources));
  assert.equal(parsed.children[0].type, "paragraph");
  if (parsed.children[0].type === "paragraph") {
    assert.equal(parsed.children[0].children[0].type, "link");
    if (parsed.children[0].children[0].type === "link") assert.equal(parsed.children[0].children[0].url, webSources[0].url);
  }
});

test("inline and fenced code, escaped samples, images and authored links remain literal", () => {
  const input = [
    "`[[cite:s1]]`",
    "```md\n[[cite:s2]]\n[example](alcuin-citation:s1)\n```",
    "    [[cite:s3]]",
    "\\[[cite:s4]]",
    "[Literal [[cite:s5]]](https://example.com)",
    "![Image [[cite:s6]]](https://example.com/image.png)",
  ].join("\n\n");
  assert.equal(portableCitationMarkdown(input, []), input);
  assert.equal(hasUnresolvedPortableCitations(input, []), false);
});

test("unknown and ambiguous citations are explicit unverified text, never invented URLs", () => {
  assert.equal(portableCitationMarkdown("Claim [[cite:missing]].", webSources), "Claim (unverified source).");
  assert.equal(portableCitationMarkdown("结论 [[cite:s9]]。", [], "zh"), "结论 （引用来源未核验）。");
  assert.equal(hasUnresolvedPortableCitations("Claim [[cite:s9]]", webSources), true);
  const ambiguous = collectCitationSources([
    event(1, { citation_id: "s1", locator: "https://example.com/first" }),
    event(2, { citation_id: "s1", locator: "https://example.com/second" }),
  ]);
  assert.equal(portableCitationMarkdown("[[cite:s1]]", ambiguous), "(unverified source)");
  assert.equal(portableCitationMarkdown("[Quoted report](alcuin-citation:s99)", []), "Quoted report (unverified source)");
});

test("knowledge evidence without a public URL retains readable location and one honest saved excerpt", () => {
  const sources = collectCitationSources([
    event(1, { citation_id: "s1", label: "Operations handbook.pdf", locator: "knowledge://private-source/private-doc#chunk-2", snippet: "Actual retrieved passage.\nDo not *invent* full text.", metadata: { kind: "knowledge", document_id: "private-doc", source_id: "private-source", chunk_index: 2, page_number: 7 } }),
    event(2, { citation_id: "s2", locator: "knowledge://private-source/private-doc#chunk-2" }),
  ]);
  const output = portableCitationMarkdown("First [[cite:s1]]. Again [[cite:s2]].", sources, "zh");
  assert.match(output, /^First （来源 1）。?/);
  assert.match(output, /Again （来源 1）/);
  assert.equal(output.match(/来源 1: Operations handbook\.pdf/g)?.length, 1);
  assert.match(output, /知识库 · 页 7 · 片段 3/);
  assert.match(output, /> Actual retrieved passage\.\n> Do not \\\*invent\\\* full text\./);
  assert.doesNotMatch(output, /knowledge:\/\/|private-doc|private-source|\[\[cite:/);
});

test("custom and knowledge Markdown links resolve only their actual reference target", () => {
  const sources = collectCitationSources([
    event(1, { citation_id: "s1", locator: "https://example.com/first" }),
    event(2, { citation_id: "s2", locator: "knowledge://a/b#chunk-0", label: "Handbook", metadata: { kind: "knowledge", source_uri: "https://example.com/handbook.pdf", chunk_index: 0 } }),
  ]);
  const output = portableCitationMarkdown("See [the **report**](#alcuin-citation-s1) and [handbook](knowledge://a/b#chunk-0).", sources);
  assert.equal(output, "See [the report](<https://example.com/first>) and [handbook](<https://example.com/handbook.pdf>).");
  assert.equal(portableCitationMarkdown("[ordinary](https://example.com/first)", sources), "[ordinary](https://example.com/first)");
});

test("reference-style citations and nested definitions become portable without leaving internal URLs", () => {
  const input = "See [Report][r], and [s2].\n\n[r]: alcuin-citation:s1\n[s2]: #alcuin-citation-s2\n[unused]: https://example.com/unused";
  const output = portableCitationMarkdown(input, webSources);
  assert.match(output, /^See \[Report\]\(<https:\/\/example\.com\/reports\/annual_\(2026\)#summary>\), and \[2\]\(<https:\/\/example\.com\/second>\)\./);
  assert.doesNotMatch(output, /alcuin-citation|\[r\]:|\[s2\]:/);
  assert.match(output, /\[unused\]: https:\/\/example\.com\/unused/);
  const nested = portableCitationMarkdown("> [Report][r]\n>\n> [r]: alcuin-citation:s1", webSources);
  assert.match(nested, /^> \[Report\]\(<https:/);
  assert.doesNotMatch(nested, /alcuin-citation/);
});

test("unsafe evidence navigation never becomes a copied executable or credential-bearing link", () => {
  const sources = collectCitationSources([
    event(1, { citation_id: "s1", label: "Unsafe source", locator: "javascript:alert(1)", snippet: "Returned excerpt" }),
    event(2, { citation_id: "s2", label: "Private URL", locator: "https://name:password@example.com/path" }),
  ]);
  const output = portableCitationMarkdown("[[cite:s1]] [[cite:s2]]", sources);
  assert.doesNotMatch(output, /javascript:|https:\/\/name:password|alcuin-citation|\[\[cite:/);
  assert.match(output, /Source 1: Unsafe source/);
  assert.match(output, /Source 2: Private URL/);
});

test("copy resolves old sources lazily and never binds the same citation ID from a different Run", async () => {
  let requests = 0;
  const own = event(1, { citation_id: "s1", locator: "https://example.com/own" });
  const foreign = { ...event(2, { citation_id: "s1", locator: "https://example.com/foreign" }), run_id: "other-run" };
  const output = await resolvePortableCitationMarkdown({ content: "Claim [[cite:s1]]", events: [foreign], runId: "run-copy", loadSources: async (id) => { requests++; assert.equal(id, "run-copy"); return [foreign, own]; } });
  assert.equal(output, "Claim [1](<https://example.com/own>)");
  assert.equal(requests, 1);
  await resolvePortableCitationMarkdown({ content: "Claim [[cite:s1]]", events: [own, foreign], runId: "run-copy", loadSources: async () => { requests++; return []; } });
  assert.equal(requests, 1, "already available evidence must not trigger another network lookup");
});

test("history failure, missing Run identity and streaming without a loader keep unmatched references honest", async () => {
  const own = event(1, { citation_id: "s1", locator: "https://example.com/own" });
  assert.equal(await resolvePortableCitationMarkdown({ content: "[[cite:s1]]", events: [], runId: "run-copy", loadSources: async () => { throw new Error("Offline"); } }), "(unverified source)");
  assert.equal(await resolvePortableCitationMarkdown({ content: "[[cite:s1]]", events: [own], runId: null, loadSources: async () => { assert.fail("no Run means no source lookup"); } }), "(unverified source)");
  assert.equal(await resolvePortableCitationMarkdown({ content: "[[cite:s1]] [[cite:s2]]", events: [own], runId: "run-copy" }), "[1](<https://example.com/own>) (unverified source)");
});

test("responses without actual citation syntax never load historical evidence during copy", async () => {
  const content = "Ordinary text and `[[cite:s1]]` as an example.";
  assert.equal(await resolvePortableCitationMarkdown({ content, events: [], runId: "run-copy", loadSources: async () => { assert.fail("no reference requires no source lookup"); } }), content);
});
