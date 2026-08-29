# Architecture Overview

Alcuin's current product center is the **Agent Foundation**: Studio, durable multi-turn conversations, deterministic context assembly, versioned Agent definitions, governed capabilities, approvals, and persisted execution events. The existing Embed implementation is frozen as a compatibility layer and is not a driver for new architecture contracts.

## Core Boundary

Alcuin separates the platform control plane from execution runtimes and domain extensions.

```text
┌─────────────────────────────────────────────────────────────┐
│                      Studio Experience                      │
│ Conversations · Context Inspector · Builder · Run Trace     │
├─────────────────────────────────────────────────────────────┤
│                       Agent Foundation                      │
│ Workspaces · Agent Versions · Threads · Messages · Runs     │
├─────────────────────────────────────────────────────────────┤
│                       Context Kernel                        │
│ Ordered Layers · Budgets · Compaction · Immutable Traces    │
├───────────────────────┬─────────────────────────────────────┤
│ Runtime Adapters      │ Capability Adapters                 │
│ LangGraph preview     │ Models · Tools · MCP/OpenAPI        │
│ OpenAI-compatible     │ Knowledge · Web Search · Approvals  │
├───────────────────────┴─────────────────────────────────────┤
│                         Infrastructure                      │
│ PostgreSQL · Qdrant · SearXNG                               │
└─────────────────────────────────────────────────────────────┘
```

This diagram is the implemented pre-alpha boundary. Workflow and multi-agent runtimes, active Skills, platform Tasks, first-class durable Artifacts, Redis, object storage, team RBAC, and production checkpoint infrastructure remain roadmap work.

## Design Direction

### Agent Definition

An immutable, versioned specification describes intended behavior. Deployment state and runtime bindings are stored separately so credentials and infrastructure can change without rewriting the definition.

### Runtime Adapter

Every runtime consumes a normalized run request and emits the same structured event stream. The repository currently contains a LangGraph ReAct preview adapter and an OpenAI-compatible streaming/tool loop. Workflow and multi-agent execution are future adapters that must use the same boundary; they are not implemented runtimes.

### Extension Manifest

Extensions declare identity, compatibility, tools, configuration schema, permissions, and lifecycle hooks. Importing an extension must not silently grant runtime access.

### Resource Scope

Agents, knowledge bases, MCP servers, secrets, conversations, and runs belong to a workspace. Services must enforce ownership before resolving or executing dependencies.

### Event Contract

Streaming output should distinguish assistant content, reasoning summaries, tool calls, tool results, citations, human interrupts, usage, errors, and terminal run state.

## Implemented Pre-alpha Boundary

The current repository is organized as a layered monorepo:

```text
apps/api                         FastAPI routes, security, composition, and transitional adapters
apps/web/app                     Next.js routing only
apps/web/features                Studio, Agents, Extensions, Runs, and Shell; frozen Embed compatibility UI
apps/web/shared                  Shared web UI, localization, and application SDK wiring
packages/python/alcuin-core      Framework-neutral Python contracts and tool envelopes
packages/python/alcuin-context   Provider-neutral context ordering, budgets, traces, and compaction
packages/python/alcuin-knowledge Domain-neutral document ingestion, Qwen embeddings, and Qdrant retrieval
packages/python/alcuin-storage   Persistence ports, Alembic migrations, and pooled PostgreSQL Store
packages/python/alcuin-web-search Provider-neutral web evidence contracts and bounded SearXNG retrieval
packages/contracts              Shared TypeScript contract vocabulary
packages/sdk                    Configurable framework-neutral REST/SSE client
packages/embed                  Frozen framework-neutral Web Component compatibility layer
packages/extension-sdk          TypeScript Extension authoring boundary
packages/sse-client             Browser-neutral resumable SSE transport
extensions/operations-copilot   Explicit domain example depending inward on Core
```

Dependencies point inward: applications may compose packages; Core cannot import applications, runtime frameworks, infrastructure clients, or domain Extensions; web shared modules cannot import features; features cannot import Next routes. Repository tests enforce these initial boundaries. Knowledge ingestion/retrieval live in `alcuin-knowledge`, and public web evidence retrieval lives in `alcuin-web-search`; runtime orchestration and the remaining connector implementations still live inside `apps/api` during staged extraction and must not be described as independent packages until they move.

Persistence consumers depend on structural `RuntimeRepository`, `ExtensionRepository`, and `KnowledgeRepository` ports from `alcuin-storage`. The API composes those ports with the pooled `PostgresStore`; services do not import psycopg or manage transactions. PostgreSQL is the single implemented control-plane database and Alembic is its only schema migration path.

A fresh Core store creates only the domain-neutral **Alcuin Starter**, with no tools or Extensions bound. Domain examples must install their Agent Definition, Manifest, and adapter explicitly; the Operations Copilot package lives under `extensions/operations-copilot`, and `examples/operations_copilot` is only its composition root. Neither is imported by `alcuin_api.main`.

The Context Kernel persists immutable user and assistant Messages within Workspace-owned Threads and assembles a normalized context envelope for every Run. Studio reuses and restores Threads, while the API records an operator-safe assembly trace with source digests and estimated token budgets. Compaction is a traceable overlay over complete conversation turns; it does not rewrite message history. The implemented composition currently supplies the platform protocol, Agent Instructions, durable conversation Messages, an optional active compaction, and allow-listed host context. Workspace Rules, User Preferences, Thread Rules, and active Skills are ordered extension points in the kernel contract, not resolved resources in the current application.

The HTTP API never exposes LangGraph state. Runtime adapters receive a normalized text-and-attachment request and emit ordered `ExecutionEvent` records. Provider events, MCP results, approval interrupts, and Artifact projections are translated at this boundary. DeepSeek vision uses native Chat Completions streaming and maps provider reasoning into `reasoning.delta` separately from visible `message.delta` output. Studio enables thinking by default, while headless clients can disable it per run.

OpenAI-compatible Chat Completions runs use a bounded tool loop. Agent tool ids are resolved through an allow-listed `ToolExecutor`, projected to provider-safe function names, validated against JSON Schema, executed with mandatory Workspace/Run context and deadlines, and returned to the model as structured tool messages. Tool request, completion, citation, and approval events are emitted independently of provider payloads. The built-in registry is configuration-driven, so unavailable dependencies are not advertised to providers.

The Workspace Tool Catalog is exposed through `GET /v1/tools` and the bootstrap payload. It combines actually configured built-in tools with every installed Extension contribution, including its canonical Agent tool id, mutation posture, contributing Manifest, and current runtime availability. The Builder binds only catalog entries and stores those ids in the immutable Agent Definition; the API independently rejects unknown tools, missing Extension bindings, and publication when a required runtime dependency is unavailable. Built-in Python adapters use their declared raw tool ids, while MCP and OpenAPI contributions use `extension.<manifest-id>.<tool-name>`.

When no model credential is configured, the LangGraph adapter runs a domain-neutral local preview. It records the request and explains that provider execution is unavailable, but never selects a domain tool or manufactures a tool result. Automated browser tests use a local OpenAI-compatible streaming fixture so the same production tool loop, adapters, approvals, citations, and Artifact event projections are exercised without contacting an external model provider.

The first registered adapter is `web.search`. It targets a configured self-hosted SearXNG JSON endpoint through the independent `alcuin-web-search` package and provides quick snippet search plus an optional bounded deep-read path. A shared HTTP client, fresh/stale TTL caches, tracking-aware canonical URL deduplication, public-address validation, response-size limits, provider/page/total deadlines, per-Run call budgets, and citation deduplication bound latency and exposure to untrusted web content. Deep-read and stale-cache fallbacks are explicitly marked as degraded evidence.

`knowledge.search` is the governed retrieval adapter. PostgreSQL stores Workspace-owned source and document lifecycle metadata plus canonical source text for controlled reindexing; Qdrant stores chunk payloads and named dense/sparse vectors. A replaceable `DocumentParser` accepts bounded TXT, Markdown, PDF, and DOCX uploads, sanitizes filenames, validates container signatures, refuses encrypted or executable content, and never persists the raw file. Ingestion normalizes the extracted text, chunks deterministically, hashes documents for idempotency, and records failed indexing without backend details. A replaceable `EmbeddingProvider` currently calls Qwen `text-embedding-v3` through DashScope and fuses its dense and sparse outputs with reciprocal-rank fusion. Requests are limited to ten texts per batch, retried within a fixed budget, and validated before indexing. Every Qdrant branch carries mandatory `workspace_id` and Agent-bound `source_id` filters, and results are checked again before they leave the adapter. Agent versions may only bind source ids owned by their Workspace, and a referenced source cannot be deleted while any immutable Agent version depends on it.

Persisted `ExecutionEvent` records remain the canonical protocol. A Run-owned sequence cursor is incremented in the same storage transaction as each event insert, so independent local database connections cannot allocate the same sequence. Every emitted frame carries its persisted sequence as the SSE `id`; reconnecting clients send that cursor through `Last-Event-ID`, while `after` remains a headless API fallback. The shared `@alcuin/sse-client` retries bounded transport failures, resumes from the last successfully handled event, and suppresses replayed sequences. The run-events endpoint also provides Alcuin's compact chat projection with `?protocol=chat`, mapping reasoning, text, tools, approvals, Artifact projections, citations, errors, and completion into data-only SSE frames. Studio consumes that projection and renders a collapsed reasoning/tool timeline followed by the Markdown answer. Completion is a terminal stream event, not an Agent tool call; domain-specific workflow states are not part of Alcuin Core.

`artifact.updated` is currently an execution event whose latest payload can be rendered in Studio's read-only Artifact panel or used by declarative Extension UI. There is no independent Artifact repository, cross-Run version history, editing contract, or Artifact lifecycle API. Likewise, manifest `skills` fields and the Context Kernel's `active_skills` layer are reserved contract shapes, not an installed and executable Skills system. Alcuin has no platform Task resource, task queue, task assignment model, or autonomous Task orchestration.

MCP processes and remote transports terminate in the API service. Browser clients only communicate with the Alcuin gateway. In the frozen Embed compatibility layer, tokens bind a workspace, published Agent Version, allowed origin, actions, and expiry. The framework-neutral Web Component creates scoped threads, consumes resumable canonical SSE, exposes host context and custom events, and can decide an approval only when the session explicitly includes `approval:decide`. This compatibility behavior remains implemented, but new product and contract work targets Studio and the Agent Foundation rather than expanding quick embedding.

Extension installation is disabled-first. The control plane persists only reviewed operations, stores credentials as secret references, performs live MCP discovery or OpenAPI reachability checks before enablement, and blocks private-network URLs unless a local-development policy explicitly allows them.

Extension UI is data, not executable code. Validated card and table blocks may bind to controlled host context, an Artifact event payload, or successful structured tool results. Form blocks create an exact requested-tool Run rather than invoking an adapter from the browser. The Runtime resolves that tool from the frozen Agent allow-list and current Workspace extension state, validates arguments, persists the same execution events, and preserves the Agent's deny/ask/auto mutation policy. The web renderer caps table rows and formats values as text; third-party HTML, scripts, components, and handlers never cross the Manifest boundary.

Platform-owned interface text is localized through a typed English/Chinese message catalog and one Web locale provider. The preference is persisted locally and updates the document language. Agent definitions, extension manifests, tool results, and other resource-owned content remain unchanged rather than being machine-translated by the interface.

`@alcuin/extension-sdk` is the authoring boundary for native extension packages. It reuses the shared TypeScript contracts, adds runtime conformance checks that cover cross-field permission and entrypoint rules, and scaffolds a runnable stdio MCP package. The generated package must still pass the control plane's independent inspection and live health checks; SDK validation never grants trust by itself.

Executable extension tools use portable Agent Definition ids in the form `extension.<manifest-id>.<tool-name>`. The runtime resolves those ids again inside the request Workspace, advertises only the Agent allow-list to the model, and rechecks extension status, health, and approved tool membership immediately before invocation. Third-party results are bounded before they enter execution events or model context.

Mutating tools pause at `approval.required`. An approval decision does not manufacture a success event: the runtime reloads the Run's frozen Agent Version and thread context, revalidates the tool against the current Workspace extension state, executes the real adapter with a one-call mutation authorization, and then persists the actual result or a controlled failure.

See [ADR-0001](adr-0001-runtime-extension-contracts.md) for the contract decisions implemented by the prototype.
See [ADR-0002](adr-0002-modular-package-boundaries.md) for the implemented package dependency rules and staged extraction order.
See [ADR-0003](adr-0003-storage-ports.md) for the verified storage boundary and adapter strategy.
See [ADR-0004](adr-0004-knowledge-package.md) for the implemented Knowledge package and retrieval boundaries.
See [ADR-0005](adr-0005-web-search-package.md) for the public web evidence contract and degradation policy.
See [ADR-0006](adr-0006-conversation-context-kernel.md) for durable Messages, context assembly, and compaction invariants.

## Remaining Decisions

- Checkpoint storage and resume semantics
- Active Skill packaging, trust, resolution, and context injection
- First-class Artifact persistence, versioning, editing, and lifecycle
- Platform Task semantics, queues, assignment, and orchestration
- Workflow and multi-agent runtime adapters
- Production PostgreSQL, Redis, managed-Qdrant, and object-storage adapters
- Team membership and RBAC beyond workspace ownership
- Signed extension packaging and distribution trust
- Evaluation and observability integration
