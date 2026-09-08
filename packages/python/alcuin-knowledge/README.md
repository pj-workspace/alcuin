# `alcuin-knowledge`

Domain-neutral knowledge ingestion and retrieval for Alcuin.

The package owns deterministic chunking, Qwen DashScope embeddings, Qdrant hybrid retrieval,
source deletion, and citation-safe result envelopes. Bounded TXT/Markdown/PDF/DOCX parsing is
provided by `alcuin-documents` and re-exported here for compatibility. PostgreSQL metadata is
accessed only through `KnowledgeRepository`; every vector operation requires a Workspace and
source filter.

FastAPI routes, Agent publication rules, and runtime tool registration remain in `apps/api`.
