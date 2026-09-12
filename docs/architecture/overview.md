# Architecture Overview

Alcuin's current product center is the **Agent Foundation**: Studio, durable conversations and Tasks, Skills/Rules, deterministic context assembly, governed capabilities, source inspection, independent Artifacts, and persisted execution events. Agent snapshots and internal revisions support correctness; version-management product flows and quick embedding remain frozen.

## Core Boundary

Alcuin separates the platform control plane from execution runtimes and domain extensions.

```text
┌─────────────────────────────────────────────────────────────┐
│                      Studio Experience                      │
│ Conversations · Task Canvas · Sources · Builder · Run Trace │
├─────────────────────────────────────────────────────────────┤
│                       Agent Foundation                      │
│ Workspaces · Agents · Threads · Messages · Runs · Tasks     │
│ Artifacts · Skills · Rules · Scoped Preferences             │
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

This diagram describes the implemented code boundary, not a production SLA or a blanket UI acceptance claim. Dynamic Task replanning, workflow/multi-agent runtimes, executable Skill scripts, full plugin hook compatibility, Redis, object storage, team RBAC, and distributed failover remain roadmap work. Artifact history/rollback/publish product flows remain paused.

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
packages/python/alcuin-customization Native Skill/Rule parsing and supported plugin importers
packages/python/alcuin-documents Bounded document parsing and editable DOCX export
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

The Context Kernel persists immutable user and assistant Messages within Workspace-owned Threads and assembles a normalized context envelope for every Run. Studio restores Threads while the API records an operator-safe assembly trace with source digests and estimated token budgets. Compaction is a traceable overlay over complete turns, not a history rewrite. Composition resolves the platform protocol, Agent Instructions, Workspace preferences, authorized Rules, selected/loaded Skills, durable messages, optional compaction, and allow-listed context. Mutable customization inputs are captured per Run so approval continuation sees the accepted configuration. Skill instructions and bounded resources can be loaded; arbitrary Skill scripts and imported plugin hooks/commands/subagents are not executed.

Thread naming runs asynchronously after an accepted user message or confirmed Task goal. It considers only saved user text and Task goals, excludes attachment bodies, execution instructions, known credentials and obvious secret patterns, and invokes a capability-free title request with a 12-second deadline. Invalid or unavailable model output falls back to a short local excerpt. `title_status` distinguishes pending, generating, and ready; atomic Workspace-scoped claims and title comparison prevent duplicate generation or overwriting custom titles. `POST /v1/threads/{thread_id}/title/ensure` explicitly repairs an older placeholder or an expired claim. `GET /v1/threads/{thread_id}/title` returns only Thread metadata and never starts naming. Studio ensures naming for the selected conversation and polls this lightweight endpoint while generation is active; naming does not depend on Run SSE events or replaying messages, and it does not generate titles for every historical conversation in the background. Internal claim tokens and lease deadlines are excluded from Thread projections. These convenience titles are not a separate audit of the conversation's complete contents.

The HTTP API never exposes LangGraph state. Runtime adapters receive a normalized text-and-attachment request and emit ordered `ExecutionEvent` records. Provider events, MCP results, approval interrupts, and Artifact projections are translated at this boundary. DeepSeek vision uses native Chat Completions streaming and maps provider reasoning into `reasoning.delta` separately from visible `message.delta` output. Studio enables thinking by default, while headless clients can disable it per run.

OpenAI-compatible Chat Completions runs use a bounded tool loop. Agent tool ids are resolved through an allow-listed `ToolExecutor`, projected to provider-safe function names, validated against JSON Schema, executed with mandatory Workspace/Run context and deadlines, and returned to the model as structured tool messages. Tool request, completion, citation, and approval events are emitted independently of provider payloads. The built-in registry is configuration-driven, so unavailable dependencies are not advertised to providers.

The Workspace Tool Catalog is exposed through `GET /v1/tools` and the bootstrap payload. It combines actually configured built-in tools with every installed Extension contribution, including its canonical Agent tool id, mutation posture, contributing Manifest, and current runtime availability. The Builder binds only catalog entries and stores those ids in the immutable Agent Definition; the API independently rejects unknown tools, missing Extension bindings, and publication when a required runtime dependency is unavailable. Built-in Python adapters use their declared raw tool ids, while MCP and OpenAPI contributions use `extension.<manifest-id>.<tool-name>`.

When no model credential is configured, the LangGraph adapter runs a domain-neutral local preview. It records the request and explains that provider execution is unavailable, but never selects a domain tool or manufactures a tool result. Automated browser tests use a local OpenAI-compatible streaming fixture so the same production tool loop, adapters, approvals, citations, and Artifact event projections are exercised without contacting an external model provider.

The first registered adapter is `web.search`. It targets a configured self-hosted SearXNG JSON endpoint through the independent `alcuin-web-search` package and provides quick snippet search plus an optional bounded deep-read path. A shared HTTP client, fresh/stale TTL caches, tracking-aware canonical URL deduplication, public-address validation, response-size limits, provider/page/total deadlines, per-Run call budgets, and citation deduplication bound latency and exposure to untrusted web content. Deep-read and stale-cache fallbacks are explicitly marked as degraded evidence.

`knowledge.search` is the governed retrieval adapter. PostgreSQL stores Workspace-owned source and document lifecycle metadata plus canonical source text for controlled reindexing; Qdrant stores chunk payloads and named dense/sparse vectors. A replaceable `DocumentParser` accepts bounded TXT, Markdown, PDF, and DOCX uploads, sanitizes filenames, validates container signatures, refuses encrypted or executable content, and never persists the raw file. Ingestion normalizes the extracted text, chunks deterministically, hashes documents for idempotency, and records failed indexing without backend details. A replaceable `EmbeddingProvider` currently calls Qwen `text-embedding-v3` through DashScope and fuses its dense and sparse outputs with reciprocal-rank fusion. Requests are limited to ten texts per batch, retried within a fixed budget, and validated before indexing. Every Qdrant branch carries mandatory `workspace_id` and Agent-bound `source_id` filters, and results are checked again before they leave the adapter. Agent versions may only bind source ids owned by their Workspace, and a referenced source cannot be deleted while any immutable Agent version depends on it.

Persisted `ExecutionEvent` records remain the canonical protocol. A Run-owned sequence cursor is incremented in the same storage transaction as each event insert, so independent local database connections cannot allocate the same sequence. Every emitted frame carries its persisted sequence as the SSE `id`; reconnecting clients send that cursor through `Last-Event-ID`, while `after` remains a headless API fallback. The shared `@alcuin/sse-client` retries bounded transport failures, resumes from the last successfully handled event, and suppresses replayed sequences. The run-events endpoint also provides Alcuin's compact chat projection with `?protocol=chat`, mapping reasoning, text, tools, approvals, Artifact projections, citations, errors, and completion into data-only SSE frames. Studio consumes that projection and renders a collapsed reasoning/tool timeline followed by the Markdown answer. Completion is a terminal stream event, not an Agent tool call; domain-specific workflow states are not part of Alcuin Core.

Studio memoizes unchanged Markdown independently of citation metadata and popover state. Citation links retain their DOM anchors while late source events update their context. Thinking content is measured through a natural-height inner ResizeObserver, and stream, resize, and spacer notifications share one scroll measurement per animation frame. These rendering optimizations do not discard execution events or introduce a delayed typewriter queue. The isolated browser streaming fixture checks progressive output, complete Markdown, and user-controlled scrolling without an external model; its timing samples do not measure provider latency.

`artifact.updated` backs an independent Workspace-owned Artifact. Runtime persistence atomically stores content, an internal revision, and its Run event; unchanged replays are no-ops. Explicit answer/artifact delimiters separate chat from up to eight generated files per Run. The final-answer marker allows tool-free answer streaming without a synthetic completion tool. A legacy plain answer remains chat and is never mirrored automatically into the Canvas. Only persisted Artifact events populate the Canvas. Markdown, plain text, JSON, and self-contained HTML are supported with bounded content; incomplete JSON is persisted only after validation at its closing delimiter.

The Artifact API supports Workspace-scoped list/get, optimistic edits after the source Run terminates, and downloads. HTML renders in a sandboxed iframe without same-origin privileges, network access, remote assets, or parent access; local inline controls are allowed. Downloaded HTML is a standalone file, and its later use outside Studio is not governed by Studio's iframe sandbox. `alcuin-documents` exports Markdown/plain text/JSON to native editable DOCX headings, lists, tables, code, and links without loading external resources. HTML-to-Word conversion and general binary generation are unsupported. Revision history, rollback, branching, and publishing remain paused product features.

`RunCitationRegistry` assigns exact `citation_id` values such as `s1` to bounded retrieved evidence, deduplicates canonical locators, and restores the same registry on approval continuation. Tool messages carry IDs, locators, and snippets; `citation.created` persists their public metadata. `[[cite:s1]]` markers resolve only against the current Run. Studio opens source details from inline references, and Word exports use the same registry numbering and append used sources. Unknown or ambiguous references must not resolve to an invented source. Knowledge document/chunk metadata supports source inspection but does not imply a PDF page. Citation identity is not an automatic proof that a source entails every claim.

`apps/api/tasks` orchestrates explicit sequential Steps above Runs. PostgreSQL persists Tasks, Plans, Attempts, links to Runs, checkpoint transitions, idempotent commands, and dispatch leases. Operator controls include pause/resume/cancel/retry and queue/steer/interrupt guidance. Completed Run boundaries are reconciled after restart; uncertain in-flight work pauses for operator verification instead of silently repeating external effects. Task creation persists `model_override` and `reasoning_effort`, validates them with the shared chat catalog policy, and revalidates them before each new Step Run. Default values use the Thread's pinned Agent definition. Multi-agent execution and distributed exactly-once scheduling are not implemented.

`POST /v1/threads/{thread_id}/task-plan-proposals` accepts a goal and optional model controls, and returns an ephemeral proposal with 1–8 editable sequential steps plus the effective model and reasoning effort. The service checks Workspace ownership and `run:create`, resolves the pinned Agent instructions and authorized Rules/Skills/preferences, and lists only available bound tools as planning context. A separate capability-free instance of the existing provider adapter generates the JSON under input, output, and total-time bounds. It neither creates a Task nor persists a Run, message, or reasoning trace; it does not retrieve conversation history or execute tools. The operator must submit the reviewed steps through Task creation and explicitly start execution. Missing credentials return 503, invalid provider output returns 502, and planning timeout returns 504; manual plans remain available. Dynamic replanning and autonomous assignment remain roadmap work.

New `task.created` and `task.plan.updated` events preserve the canonical goal and complete step title, description, and position snapshots, so confirmed plan content remains reconstructable after later edits. These fields are additive; historical events are not backfilled. Step attempts link to Runs, whose tool, citation, and Artifact events supply execution evidence. Proposal generation remains ephemeral: the original model draft, the changes made before confirmation, and authenticated editor attribution are not recorded by this plan snapshot feature.

Studio treats Task Artifact evidence, checkpoint changes, and terminal status as scoped refresh hints for the thread's canonical Artifact resources. Background refreshes merge resources without resetting an open editor or changing the selected Canvas tab. Task evidence summaries are not Artifact bodies; this refresh bridge does not imply token-level streaming of Task outputs.

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
See [ADR-0009](adr-0009-editable-artifact-resources.md) for durable editable Artifact resources, atomic runtime persistence, and optimistic edits.
See [ADR-0010](adr-0010-independent-artifacts-and-evidence.md) for separate chat/Artifact output, citation identity, sandboxed HTML, Word export, and durable Task model controls.
See [Source evidence](source-evidence.md) for citation display, portable copying, bounded history recovery, and factuality boundaries.

## Remaining Decisions

- Distributed worker leases, failover, and production recovery verification
- Executable Skill resource sandboxing and broader plugin compatibility
- General binary Artifact lifecycle and document-format fidelity
- Dynamic Task replanning and autonomous assignment
- Workflow and multi-agent runtime adapters
- Production PostgreSQL, Redis, managed-Qdrant, and object-storage adapters
- Team membership and RBAC beyond workspace ownership
- Signed extension packaging and distribution trust
- Evaluation and observability integration
