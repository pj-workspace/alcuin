# `alcuin-knowledge`

Domain-neutral knowledge ingestion and retrieval for Alcuin.

The package owns bounded TXT/Markdown/PDF/DOCX parsing, deterministic chunking, Qwen
DashScope embeddings, Qdrant hybrid retrieval, source deletion, and citation-safe result
envelopes. PostgreSQL metadata is accessed only through `KnowledgeRepository`; every vector
operation requires a Workspace and source filter.

FastAPI routes, Agent publication rules, and runtime tool registration remain in `apps/api`.
