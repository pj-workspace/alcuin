# ADR-0005: Treat public web retrieval as a bounded evidence capability

## Status

Accepted and implemented in the pre-alpha.

## Context

The initial `web.search` adapter could query SearXNG and optionally read result pages, but the
implementation lived in `apps/api`, exposed a provider-shaped method, and represented provider
failure only as an exception. A reusable Agent capability needs a stable evidence contract and must
make partial or stale retrieval visible to the runtime.

## Decision

`packages/python/alcuin-web-search` owns:

- provider-neutral `WebSearchQuery` and `WebSearchResponse` contracts;
- a SearXNG JSON adapter using the documented `/search` endpoint and `format=json`;
- quick snippet retrieval and a bounded deep-read mode;
- canonical URL normalization, tracking-parameter removal, and result deduplication;
- per-provider, per-page, and whole-operation deadlines;
- public-address validation, response byte/character limits, and non-executable text extraction;
- fresh and stale-if-error caches with explicit `degraded` and warning fields;
- citation-safe results using canonical public URLs.

`apps/api` maps environment configuration into the package and registers the `web.search` tool.
The package imports only `alcuin-core` tool envelopes and its HTTP client; it does not import
FastAPI, runtime frameworks, storage adapters, or domain Extensions.

Deep-read failures keep the original search result and mark the response `deep_read_partial`.
SearXNG timeouts or transient failures may return an exact-query stale cache entry marked
`stale_cache`. Without cached evidence, the tool fails with a controlled error. The adapter never
manufactures search results and never hides degraded evidence behind a success-only response.

## Consequences

- Studio, Embed, and future host applications can share the same normalized evidence contract.
- Runtime traces and model context can distinguish fresh, partial, and stale retrieval.
- SearXNG is the only implemented provider adapter; additional providers can target the same public
  contract without changing Agent Definitions.
- Search ranking evaluation, multi-provider fan-out, JavaScript rendering, authenticated browsing,
  and durable distributed caches are not implemented by this decision.
