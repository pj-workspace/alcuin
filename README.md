# Alcuin

> Intelligence, composed.

Alcuin is an extensible foundation for building, operating, and evolving AI agents. It brings models, knowledge, tools, policies, and workflows into one coherent system without locking applications to a single provider or domain.

The project is named after Alcuin of York, a scholar and organizer of knowledge whose work helped shape the Carolingian Renaissance.

## Vision

Most agent applications begin as a prompt connected to a few tools. As they grow, model routing, retrieval, credentials, human approval, execution state, observability, and permissions become scattered across the codebase.

Alcuin treats those concerns as first-class platform capabilities:

- **Composable** — assemble an agent from a model, instructions, tools, knowledge, memory, and runtime policy.
- **Extensible** — add capabilities through built-in extensions, MCP servers, and pluggable runtimes.
- **Model-agnostic** — support multiple LLM providers behind a consistent contract.
- **Knowledge-native** — bind governed RAG collections and structured data sources to agents.
- **Human-guided** — support approvals, structured questions, secure credential collection, and resumable runs.
- **Observable** — preserve streaming events, tool traces, citations, usage, failures, and execution history.

## Product Surface

Alcuin is planned as a full-stack agent workspace rather than a thin framework:

```text
Experience
  Chat · Agent Studio · Knowledge · Tools & MCP · Runs

Control Plane
  Agent definitions · Versions · Access · Policies · Secrets

Runtime
  ReAct · Workflows · Multi-agent orchestration · Streaming · Checkpoints

Integrations
  Model providers · Built-in tools · MCP servers · RAG · External APIs

Infrastructure
  PostgreSQL · Redis · Qdrant · Object storage
```

## Planned Capabilities

- Versioned agent definitions and lifecycle management
- Per-agent model, prompt, tool, knowledge, and runtime configuration
- Python extension manifests and dynamic tool discovery
- MCP over Streamable HTTP, SSE, and local stdio
- Multi-provider chat and embedding model registry
- Knowledge ingestion, vector retrieval, reranking, and citations
- Streaming runs with tool-call and reasoning events
- Human-in-the-loop questions, forms, approvals, and resumable execution
- Workspace isolation, role-based access, and scoped credentials
- Run history, token usage, cost accounting, and observability hooks
- ReAct as the default runtime, with workflow and multi-agent runtimes as extensions

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

- A Next.js Agent Studio with a conversation timeline, Artifact canvas, run trace, approvals, light/dark themes, and responsive navigation
- Workspace-scoped Agent creation and selection with versioned declarative definitions behind a FastAPI control plane
- An authoritative Workspace Tool Catalog that exposes configured built-ins and installed Extension tools with runtime availability, mutation metadata, and contributing Extension ownership
- A replaceable runtime boundary with a LangGraph ReAct demo adapter and an optional OpenAI-compatible streaming adapter
- A bounded OpenAI-compatible tool loop with Agent allow-lists, JSON Schema validation, workspace context, per-tool deadlines, call budgets, and structured results
- A built-in `web.search` adapter for self-hosted SearXNG with quick/deep modes, TTL caches, URL deduplication, bounded page extraction, SSRF guards, and citation events
- A built-in `knowledge.search` adapter with Builder-based document import, deterministic chunking, Qwen dense+sparse hybrid retrieval, Agent-version source binding, and `knowledge://` citations
- A persisted English/Chinese interface switch across Studio, Builder, Extensions, Runs, and Embed, while keeping Agent and extension-owned content unchanged
- Persisted normalized execution events with resumable SSE delivery and Alcuin's compact chat stream projection
- Extension inspection, disabled-first installation, permission review, live health state, and enable/disable lifecycle
- A typed `@alcuin/extension-sdk` with offline conformance validation, stable tool-id helpers, and a runnable stdio MCP scaffold
- Workspace-scoped MCP/OpenAPI tool resolution from enabled extensions into Agent Definitions and model tool loops, with runtime state rechecks and bounded results
- MCP discovery and invocation over stdio, SSE, and Streamable HTTP
- OpenAPI JSON/YAML import from request bodies or URLs and read-only execution, with mutating operations routed to approval-gated runs
- Origin-bound Embed Session tokens and a framework-neutral `<alcuin-agent>` Web Component with `lang="en|zh-CN"` localization
- Standard `Last-Event-ID` recovery shared by Studio and Embed, with bounded reconnects and replay suppression
- Declarative extension card/table/form blocks with scoped data binding and approval-gated Tool Runs
- An explicit Operations Copilot example proving that a domain Extension can run in Studio and an embedded host without entering Alcuin Core
- A framework-neutral Python `alcuin-core`, configurable `@alcuin/sdk`, enforced web feature boundaries, and an independently packaged Operations example
- PostgreSQL-only control-plane persistence with Workspace-scoped Repository ports, pooled connections, Alembic migrations, and atomic Run event sequencing

The contracts are versioned but not yet stable. PostgreSQL stores control-plane metadata and canonical knowledge text, while Qdrant is the implemented knowledge vector index. Redis remains roadmap infrastructure rather than a claimed runtime dependency.

## Quick Start

Requirements: Node.js 22+, pnpm 11+, Python 3.11+, `uv`, and Docker.

```bash
cp .env.example .env
pnpm install
uv sync --project apps/api
pnpm dev:prepare
pnpm dev
```

Open [http://localhost:3000/studio](http://localhost:3000/studio). The API and interactive OpenAPI reference run at [http://localhost:8000/docs](http://localhost:8000/docs).

No model credential is required for the domain-neutral Alcuin Starter preview. The preview records the request but never invokes a bound tool or invents a result. DeepSeek is the first configured provider preset: put the key in the ignored local `.env` as `ALCUIN_DEEPSEEK_API_KEY`; the default endpoint is `https://api.deepseek.com`, model is the experimental `deepseek-v4-flash-vision-exp`, and protocol is Chat Completions. Studio enables thinking and renders its native stream in a compact, collapsible trace before the final Markdown output; API clients can disable thinking per run. Studio accepts up to four PNG, JPEG, WebP, or GIF attachments of 5 MiB each and sends only the active run's image data to the configured provider. DeepSeek V4 Flash text remains selectable, while generic OpenAI-compatible endpoints remain available through the `ALCUIN_OPENAI_*` variables.

Operations Copilot is intentionally separate from Core. Run the optional example API with `uv run --project apps/api uvicorn examples.operations_copilot.app:app --reload --port 8000`; see [its README](examples/operations_copilot/README.md).

Public web search uses the bundled SearXNG service and does not require another API key. For local development, start it with `docker compose up -d searxng` and keep `ALCUIN_SEARXNG_URL=http://localhost:9888` in the ignored `.env`. Compose-connected API containers use `http://searxng:8080`. Quick search returns normalized snippets; deep search additionally reads at most three validated public pages under strict byte, time, and output limits.

Workspace knowledge uses the bundled Qdrant service at `http://localhost:6333` and Qwen `text-embedding-v3` through DashScope. Put `ALCUIN_DASHSCOPE_API_KEY` in the ignored local `.env`; Workspace-specific DashScope endpoints can be set with `ALCUIN_DASHSCOPE_HTTP_API_URL`. In **Agents → Knowledge**, upload TXT, Markdown, PDF, or DOCX files—or paste text—then bind the resulting source and publish the Agent version. Uploads are signature-checked, limited to 8 MiB, parsed without executing embedded content, and reduced to at most two million characters of canonical text; raw files are not persisted. `knowledge.search` fuses Qwen dense and sparse vectors only across the bound source IDs and active Workspace. Embedding requests are batched, bounded, validated, and never expose provider credentials or raw provider errors.

Run the complete verification suite with:

```bash
pnpm -r test
pnpm -r lint
pnpm -r build
uv run --project apps/api pytest
pnpm test:e2e
```

Docker users can start the prototype with `docker compose up --build`. PostgreSQL and Redis can be added with `docker compose --profile platform-infra up --build`.

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

## Technology Direction

The initial implementation is expected to use:

- **Backend:** Python, FastAPI, LangGraph, LangChain, SQLAlchemy
- **Frontend:** Next.js, React, TypeScript, Tailwind CSS
- **Data:** PostgreSQL, Redis, Qdrant
- **Protocols:** MCP, SSE, OpenAPI
- **Operations:** Docker Compose, Alembic, OpenTelemetry-compatible traces

The pre-alpha uses PostgreSQL 17 through Docker Compose and Alembic. Repository ports keep runtime and service code independent of connection and transaction details, but PostgreSQL is the single implemented control-plane database.

## Security

Please do not open public issues for security vulnerabilities. Follow the private reporting process in [SECURITY.md](SECURITY.md).

## License

A license will be selected before the first public release. Until then, all rights are reserved.
