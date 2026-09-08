# Alcuin

> Intelligence, composed.

Alcuin is an extensible Agent Foundation for building and operating domain-neutral AI agents. Its current product center is Studio: persistent conversations, Skills and Rules, governed tools and knowledge, durable Tasks, independent Artifacts, source inspection, and auditable execution events. Internal definition snapshots and concurrency tokens protect execution; product-facing version management and quick embedding are paused.

The project is named after Alcuin of York, a scholar and organizer of knowledge whose work helped shape the Carolingian Renaissance.

## Vision

Most agent applications begin as a prompt connected to a few tools. As they grow, model routing, retrieval, credentials, human approval, execution state, observability, and permissions become scattered across the codebase.

Alcuin aims to treat those concerns as first-class platform capabilities:

- **Composable** — assemble an agent from a model, instructions, tools, knowledge, memory, and runtime policy.
- **Extensible** — add capabilities through built-in extensions, MCP servers, and pluggable runtimes.
- **Model-agnostic** — support multiple LLM providers behind a consistent contract.
- **Knowledge-native** — bind governed RAG collections and structured data sources to agents.
- **Human-guided** — support approvals, structured questions, secure credential collection, and resumable runs.
- **Observable** — preserve streaming events, tool traces, citations, usage, failures, and execution history.

## Agent Foundation

The current pre-alpha product path is a full-stack, Studio-centered Agent workspace rather than a thin framework:

```text
Studio Experience
  Conversations · Task progress · Source inspection · Artifact Canvas · Agent Builder

Agent Foundation
  Agent definitions · Threads · Messages · Tasks · Artifacts · Context · Run events

Governed Capabilities
  Model adapters · Skills/Rules · MCP/OpenAPI · Knowledge · Web search · Approvals

Implemented Infrastructure
  PostgreSQL · Qdrant · SearXNG
```

The existing `@alcuin/embed` Web Component and Embed Session API remain in the repository as a frozen compatibility layer. Third-party quick embedding is not the current product route and does not drive new foundation contracts.

## Roadmap, Not Current Capability

- Skill script execution and full third-party plugin hooks, commands, and subagents. Installed Skill instructions and resources already participate in scoped context assembly.
- Automatic Task decomposition, dynamic planning, assignment, and distributed worker scheduling. Current Tasks execute explicit steps sequentially above durable Runs.
- Arbitrary binary Artifact generation, collaborative editing, and broad document-format fidelity. Text Artifacts already support editing, isolated HTML preview, and editable DOCX export.
- Workflow and multi-agent runtimes beyond the current replaceable runtime boundary.
- Team membership and RBAC, production secrets infrastructure, cost accounting, evaluation, and full observability integration.
- Redis, object storage, and production multi-worker failover guarantees. PostgreSQL Task checkpoints and explicit restart recovery are implemented.

## Architecture Principles

1. **Definitions are declarative; runtimes are replaceable.**
2. **Domain behavior belongs in extensions, not the platform core.**
3. **Every tool is permissioned, bounded, and observable.**
4. **Knowledge access is scoped to the active user and workspace.**
5. **Human decisions are explicit events, not hidden prompt conventions.**
6. **Provider-specific behavior stays behind stable interfaces.**

See [Architecture Overview](docs/architecture/overview.md), [Extension Authoring](docs/extensions/authoring.md), and [Project Vision](docs/vision.md).

## Project Status

Alcuin is a working **pre-alpha prototype**. It currently includes:

- A Next.js Agent Studio with durable multi-turn Threads and Messages, refresh restoration, run trace, approval handling, context inspection, light/dark themes, and responsive navigation
- A provider-neutral Context Kernel with deterministic layer ordering, bounded input budgets, immutable per-Run context snapshots, traceable complete-turn compaction, and Workspace-scoped conversation persistence
- Workspace-owned Artifacts with independent storage, optimistic edits, multiple outputs per Run, live Canvas updates, isolated HTML preview, and editable Word export from Markdown/plain text/JSON; ordinary chat answers do not automatically become Artifacts
- Run-local citation IDs shared by tool evidence, inline answer references, source inspection, Markdown Artifacts, and Word references; sources include available snippets and metadata without invented PDF pages or claims of automatic fact verification
- Installed native Skills, always/conditional/manual Rules, Workspace preferences, and scoped per-Run snapshots; plugin importers adapt supported declarative resources without executing arbitrary imported scripts or hooks
- Durable sequential Tasks with explicit steps, attempts, checkpoints, approvals, pause/resume/cancel/retry, and user guidance; selected model and thinking effort persist through steps and recovery
- Workspace-scoped Agent creation and selection with versioned declarative definitions behind a FastAPI control plane
- An authoritative Workspace Tool Catalog that exposes configured built-ins and installed Extension tools with runtime availability, mutation metadata, and contributing Extension ownership
- A replaceable runtime boundary with a LangGraph ReAct demo adapter and an optional OpenAI-compatible streaming adapter
- A bounded OpenAI-compatible tool loop with Agent allow-lists, JSON Schema validation, workspace context, per-tool deadlines, call budgets, and structured results
- An independently packaged `web.search` capability for self-hosted SearXNG with quick/deep modes, tracking-aware URL deduplication, bounded page extraction, SSRF guards, explicit stale/partial degradation, and citation events
- A built-in `knowledge.search` adapter with Builder-based document import, deterministic chunking, Qwen dense+sparse hybrid retrieval, Agent-version source binding, and `knowledge://` citations
- A persisted English/Chinese interface switch across Studio, Builder, Extensions, Runs, and the frozen Embed compatibility UI, while keeping Agent and extension-owned content unchanged
- Persisted normalized execution events with resumable SSE delivery and Alcuin's compact chat stream projection
- Extension inspection, disabled-first installation, permission review, live health state, and enable/disable lifecycle
- A typed `@alcuin/extension-sdk` with offline conformance validation, stable tool-id helpers, and a runnable stdio MCP scaffold
- Workspace-scoped MCP/OpenAPI tool resolution from enabled extensions into Agent Definitions and model tool loops, with runtime state rechecks and bounded results
- MCP discovery and invocation over stdio, SSE, and Streamable HTTP
- OpenAPI JSON/YAML import from request bodies or URLs and read-only execution, with mutating operations routed to approval-gated runs
- A frozen Embed compatibility layer consisting of origin-bound Embed Session tokens and a framework-neutral `<alcuin-agent>` Web Component; it is retained for compatibility, not active product development
- Standard `Last-Event-ID` recovery in Studio and the frozen Embed layer, with bounded reconnects and replay suppression
- Declarative extension card/table/form blocks with scoped data binding and approval-gated Tool Runs
- An explicit Operations Copilot example proving that a domain Extension can run without entering Alcuin Core
- Framework-neutral Python `alcuin-core`, independently packaged `alcuin-knowledge`, configurable `@alcuin/sdk`, enforced web feature boundaries, and an independently packaged Operations example
- PostgreSQL-only control-plane persistence with Workspace-scoped Repository ports, pooled connections, Alembic migrations, and atomic Run event sequencing

The contracts are not yet stable. PostgreSQL stores control-plane metadata, Tasks, Artifact text, and canonical knowledge text; Qdrant is the implemented knowledge vector index. Redis remains roadmap infrastructure. Internal revisions do not introduce a version-history product workflow.

Provider output uses the same `ALCUIN_CONTEXT_RESERVED_OUTPUT_TOKENS` budget reserved by
context assembly: 16,384 tokens by default, with smaller implicit reserves for small context
windows. Explicit configured budgets take precedence. Chat Completions and Responses receive
this shared limit without a model-specific token cap. Truncated, filtered, failed, or empty
answers produce `run.failed` while retaining partial output; reasoning alone is not completion.

## Quick Start

Requirements: Node.js 22+, pnpm 11+, Python 3.11+, `uv`, and Docker.

```bash
cp .env.example .env
pnpm install
uv sync --project apps/api
pnpm dev
```

Open [http://localhost:3000/studio](http://localhost:3000/studio). The API and interactive OpenAPI reference run at [http://localhost:8000/docs](http://localhost:8000/docs).

`pnpm dev` starts PostgreSQL, Qdrant, and SearXNG, applies Alembic migrations, and then runs the API and web apps. It reuses an existing Compose PostgreSQL port or selects a free local port automatically. Press `Ctrl+C` to stop the complete development stack; set `ALCUIN_DEV_KEEP_SERVICES=1` only when the infrastructure should remain running.

No model credential is required for the domain-neutral Alcuin Starter preview. The preview records the request but never invokes a bound tool or invents a result. DeepSeek is the first configured provider preset: put the key in the ignored local `.env` as `ALCUIN_DEEPSEEK_API_KEY`; the default endpoint is `https://api.deepseek.com`, model is the experimental `deepseek-v4-flash-vision-exp`, and protocol is Chat Completions. Studio enables thinking and renders its native stream in a compact, collapsible trace before the final Markdown output; API clients can disable thinking per run. Studio accepts up to four PNG, JPEG, WebP, or GIF attachments of 5 MiB each and sends only the active run's image data to the configured provider. DeepSeek V4 Flash text remains selectable, while generic OpenAI-compatible endpoints remain available through the `ALCUIN_OPENAI_*` variables.

Operations Copilot is intentionally separate from Core. Run the optional example API with `uv run --project apps/api uvicorn examples.operations_copilot.app:app --reload --port 8000`; see [its README](examples/operations_copilot/README.md).

Public web search uses the bundled SearXNG service and does not require another API key. `pnpm dev` starts it at `http://localhost:9888`; keep `ALCUIN_SEARXNG_URL` pointed there for the locally run API. Quick search returns normalized, deduplicated snippets. Deep search additionally reads at most three validated public pages under strict byte, time, and output limits. Partial page reads and stale-cache fallback remain usable but are explicitly marked as degraded evidence.

Workspace knowledge uses the bundled Qdrant service at `http://localhost:6333` and Qwen `text-embedding-v3` through DashScope. Put `ALCUIN_DASHSCOPE_API_KEY` in the ignored local `.env`; Workspace-specific DashScope endpoints can be set with `ALCUIN_DASHSCOPE_HTTP_API_URL`. In **Agents → Knowledge**, upload TXT, Markdown, PDF, or DOCX files—or paste text—then bind the resulting source and publish the Agent version. Uploads are signature-checked, limited to 8 MiB, parsed without executing embedded content, and reduced to at most two million characters of canonical text; raw files are not persisted. `knowledge.search` fuses Qwen dense and sparse vectors only across the bound source IDs and active Workspace. Embedding requests are batched, bounded, validated, and never expose provider credentials or raw provider errors.

Run the complete verification suite with:

```bash
pnpm lint
pnpm build
pnpm test
pnpm test:e2e
```

Docker Compose provides the local PostgreSQL, Qdrant, and SearXNG dependencies. `pnpm dev` owns the complete local lifecycle; `pnpm dev:prepare` remains available when only infrastructure and migrations are needed.

## Development Workflow

```text
main
└── develop
    ├── feat/*
    ├── fix/*
    ├── refactor/*
    ├── docs/*
    └── chore/*
```

- `main` contains reviewed release-ready history and is protected.
- `develop` is the integration branch.
- All implementation work starts from `develop` on a short-lived branch.
- Pull requests target `develop`; release pull requests promote `develop` into `main`.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making changes.

## Technology Boundary

The current pre-alpha uses:

- **Backend:** Python, FastAPI, LangGraph, LangChain, SQLAlchemy
- **Frontend:** Next.js, React, TypeScript, Tailwind CSS
- **Data and retrieval:** PostgreSQL, Qdrant, SearXNG
- **Protocols:** MCP, SSE, OpenAPI
- **Operations:** Docker Compose and Alembic

Repository ports keep runtime and service code independent of connection and transaction details, but PostgreSQL 17 is the single implemented control-plane database. Redis, object storage, and OpenTelemetry integration remain roadmap items.

## Security

Please do not open public issues for security vulnerabilities. Follow the private reporting process in [SECURITY.md](SECURITY.md).

## License

A license will be selected before the first public release. Until then, all rights are reserved.
