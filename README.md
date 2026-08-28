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

See [Architecture Overview](docs/architecture/overview.md) and [Project Vision](docs/vision.md).

## Project Status

Alcuin is in **pre-alpha foundation design**. The first milestone is a domain-neutral extraction of a proven FastAPI, LangGraph, RAG, MCP, and Next.js application architecture.

The public API and storage schema are not stable yet.

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

Technology choices remain subject to architecture review during the foundation milestone.

## Security

Please do not open public issues for security vulnerabilities. Follow the private reporting process in [SECURITY.md](SECURITY.md).

## License

A license will be selected before the first public release. Until then, all rights are reserved.

