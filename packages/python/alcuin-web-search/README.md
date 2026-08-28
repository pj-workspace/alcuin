# `alcuin-web-search`

Domain-neutral public web retrieval for Alcuin.

The package owns the normalized query/result contract, SearXNG JSON adapter, canonical URL
deduplication, bounded optional page reads, public-address validation, TTL/stale caches, explicit
degraded results, and citation-safe tool envelopes. FastAPI and runtime registration remain in
`apps/api`.
