# ADR-0004: Package governed knowledge as a replaceable capability

## Status

Accepted and implemented in the pre-alpha.

## Context

The first knowledge path proved file ingestion, Qwen embeddings, Qdrant retrieval, Agent
bindings, and citations, but its parser and vector adapter lived inside `apps/api`. That made a
working capability look like an API implementation detail and made reuse from another host harder.

Alcuin needs a domain-neutral knowledge boundary that can support a mineral-risk Agent, an
operations Agent, or another embedded product without moving domain prompts or scoring rules into
Core.

## Decision

`packages/python/alcuin-knowledge` owns:

- bounded TXT, Markdown, PDF, and DOCX extraction;
- deterministic normalization and overlapping chunking;
- the replaceable `EmbeddingProvider` and `KnowledgeIndex` protocols;
- the Qwen DashScope `text-embedding-v3` dense+sparse adapter;
- Qdrant collection validation, document-level vector replacement, hybrid RRF retrieval, and
  source deletion;
- Workspace/source filtering, result revalidation, and `knowledge://` citation locators.

The package depends inward on `alcuin-core` tool envelopes and the `KnowledgeRepository` metadata
port from `alcuin-storage`. It does not import FastAPI, runtime frameworks, or domain Extensions.
`apps/api` remains the composition root: it maps environment settings into package configuration,
registers `knowledge.search`, enforces Agent publication rules, and exposes management routes.

PostgreSQL is canonical for Workspace-owned source/document lifecycle metadata and normalized text.
Qdrant is the implemented vector index. Every query contains mandatory `workspace_id` and bound
`source_id` filters, and returned payloads are checked again before leaving the adapter. Reindexing
embeds the new chunks before replacing every prior point for that document, preventing stale chunk
citations after a smaller revision.

The default automated suite uses a real isolated Qdrant service with deterministic embeddings. A
separate live smoke check verified the configured Qwen provider without persisting or printing its
credential.

## Consequences

- The same knowledge package can be composed by Studio, Embed, or another host application.
- Missing Qdrant or DashScope configuration makes the tool unavailable rather than silently falling
  back to a fake retriever.
- Raw uploaded files are not retained; PostgreSQL keeps normalized text for controlled reindexing.
- OCR, scanned-PDF extraction, reranking, asynchronous ingestion jobs, and external knowledge
  connectors are not implemented by this decision.

The Qwen request shape follows Alibaba Cloud Model Studio's synchronous text-embedding API, while
the hybrid query follows Qdrant's named dense/sparse prefetch plus reciprocal-rank fusion contract.
