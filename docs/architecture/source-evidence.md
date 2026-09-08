# Source evidence

Alcuin keeps retrieved evidence separate from the assistant's claims. A `citation.created`
event means a tool returned a source. It does not mean that source verifies every sentence
in the answer.

## Runtime contract

`RunCitationRegistry` in `alcuin_api/evidence.py` belongs to one Run. It assigns stable
`citation_id` values (`s1`, `s2`, …), deduplicates web-page locators while preserving query
strings, and keeps separate knowledge chunk locators distinct. Resuming a Run seeds the
registry from that Run's persisted citation events; another Run's events are ignored.

Tool results reach the model as a bounded JSON envelope with `data`, `evidence`, an
untrusted-reference notice, and an explicit omitted-evidence count. Each included evidence
row preserves its complete ID and locator and a bounded excerpt. The default envelope cap
is 24,000 characters; large tool data is represented by an explicitly truncated prefix.

The model associates a claim with evidence using `[[cite:s1]]`. The Studio also recognizes
`[label](alcuin-citation:s1)` and existing links that match a retrieved locator. It never
guesses a source from arbitrary `[1]`, `(K1)`, list order, or a model-invented URL. Missing
or ambiguous IDs render as unverified references.

## Studio and history

The citation feature owns source normalization, inline previews, expandable evidence
lists, and history loading. Markdown processing only transforms text nodes, leaving code
samples unchanged. Source titles and excerpts render as text; original links accept only
HTTP(S) URLs without embedded credentials. Knowledge evidence displays the stored
document title, passage index, and page number when the tool supplied one.

`GET /v1/threads/{thread_id}` includes `citation_events` for the most recent 50 Runs
represented in the returned messages. The repository performs one Workspace-and-Thread
scoped query, with at most 128 citation events per selected Run. This does not change the
existing complete-message-history behavior.

An older response loads `GET /v1/runs/{run_id}/citations` when the user opens a missing
reference or its source entry. That endpoint checks Run ownership and Agent scope and
returns at most 128 source events, never reasoning or full tool traces. The browser
deduplicates requests and caches by API, Workspace, and Run for two minutes, retaining at
most 64 entries. It does not automatically issue one request per historical Run.

Conversation and Markdown Artifact views use the same citation renderer. An Artifact's
source Run supplies its evidence; citations from the currently active, unrelated Run must
not be reused even when both Runs have an `s1` marker.

## Copy and document export

Conversation and Markdown Artifact copy use a shared, source-position-aware Markdown
formatter. Only actual citation syntax is converted: a matching public source becomes
a portable HTTP(S) Markdown link; a source without a public link keeps its document/
passage information and saved retrieval excerpt. Unknown or ambiguous references become
explicit unverified text, never an invented link. Authored Markdown outside these
references, including code samples containing literal protocol markers, is preserved.

Copy resolves missing historical evidence through the same scoped source cache. A
generating Artifact uses its currently received evidence without caching an incomplete
Run. A failed history lookup does not block copying; unmatched references remain visibly
unverified. Clipboard failure is reported, not acknowledged as a successful copy.

Markdown and Word downloads remain server-generated. Copy and these document exports
resolve citations against the Artifact's source Run, not another conversation's current
sources. This preserves reference provenance; it does **not** automatically verify that
the cited text supports the claim. HTML and plain-text copies retain their authored
contents rather than interpreting them as Markdown.

## Current boundaries

- Excerpts are retrieval snapshots, not an automatic entailment or factuality score.
- A knowledge chunk index is not a PDF page number. Page numbers are shown only if present.
- A source without an original URL remains inspectable through its saved excerpt and
  document/passage metadata; this does not provide a full original-document viewer.
- Sources from old Runs without persisted IDs remain browsable and match exact source
  links. The UI does not fabricate IDs for old numeric markers.
- Thread message pagination and full-document highlighting are separate follow-up work.

Regression coverage includes source-ID isolation, replay deduplication, Markdown code
literals, strict model-envelope bounds, bounded history loading, and Workspace/Thread/
Agent access checks.
