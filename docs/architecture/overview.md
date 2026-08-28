# Architecture Overview

## Core Boundary

Alcuin separates the platform control plane from execution runtimes and domain extensions.

```text
┌─────────────────────────────────────────────────────────────┐
│                       Product Experience                    │
│ Chat · Agent Studio · Knowledge · Tool Catalog · Run Trace │
├─────────────────────────────────────────────────────────────┤
│                         Control Plane                       │
│ Workspaces · Agent Definitions · Versions · Access · Vault │
├─────────────────────────────────────────────────────────────┤
│                       Runtime Protocol                      │
│ Inputs · Events · Checkpoints · Interrupts · Results       │
├───────────────────────┬─────────────────────────────────────┤
│ Runtime Adapters      │ Capability Adapters                 │
│ ReAct · Workflow      │ Models · Tools · MCP · Knowledge    │
│ Multi-agent           │ Memory · Observability              │
├───────────────────────┴─────────────────────────────────────┤
│                         Infrastructure                      │
│ PostgreSQL · Redis · Qdrant · Object Storage               │
└─────────────────────────────────────────────────────────────┘
```

## Design Direction

### Agent Definition

An immutable, versioned specification describes intended behavior. Deployment state and runtime bindings are stored separately so credentials and infrastructure can change without rewriting the definition.

### Runtime Adapter

Every runtime consumes a normalized run request and emits the same structured event stream. ReAct is the initial adapter; workflow and multi-agent execution should use the same boundary.

### Extension Manifest

Extensions declare identity, compatibility, tools, configuration schema, permissions, and lifecycle hooks. Importing an extension must not silently grant runtime access.

### Resource Scope

Agents, knowledge bases, MCP servers, secrets, conversations, and runs belong to a workspace. Services must enforce ownership before resolving or executing dependencies.

### Event Contract

Streaming output should distinguish assistant content, reasoning summaries, tool calls, tool results, citations, human interrupts, usage, errors, and terminal run state.

## Implemented Pre-alpha Boundary

The current repository is organized as a small monorepo:

```text
apps/api          FastAPI control plane, runtime orchestration, SSE, MCP gateway
apps/web          Next.js Studio, Builder, Extensions, Runs, Embed Playground
packages/contracts Shared TypeScript contract vocabulary
packages/embed     Framework-neutral Web Component
extensions         Example domain-neutral extension packages
```

The HTTP API never exposes LangGraph state. Runtime adapters receive a normalized text-and-attachment request and emit ordered `ExecutionEvent` records. Provider events, MCP results, approval interrupts, and artifacts are translated at this boundary. DeepSeek vision uses native Chat Completions streaming and maps provider reasoning into `reasoning.delta` separately from visible `message.delta` output. Studio enables thinking by default, while headless clients can disable it per run.

OpenAI-compatible Chat Completions runs use a bounded tool loop. Agent tool ids are resolved through an allow-listed `ToolExecutor`, projected to provider-safe function names, validated against JSON Schema, executed with mandatory Workspace/Run context and deadlines, and returned to the model as structured tool messages. Tool request, completion, citation, and approval events are emitted independently of provider payloads. The built-in registry is configuration-driven, so unavailable dependencies are not advertised to providers.

The first registered adapter is `web.search`. It targets a configured self-hosted SearXNG JSON endpoint and provides quick snippet search plus an optional bounded deep-read path. A shared HTTP client, TTL caches, canonical URL deduplication, public-address validation, response-size limits, per-page deadlines, per-Run call budgets, and citation deduplication bound latency and exposure to untrusted web content.

Persisted `ExecutionEvent` records remain the canonical protocol. The run-events endpoint also provides a lightweight TCM-compatible projection with `?protocol=tcm`, mapping reasoning, text, tools, approvals, artifacts, citations, errors, and completion into data-only SSE frames. Studio consumes that projection and renders a collapsed reasoning/tool timeline followed by the Markdown answer. Completion is a terminal stream event, not an Agent tool call; domain-specific TCM workflow states are not part of Alcuin Core.

MCP processes and remote transports terminate in the API service. Browser clients only communicate with the Alcuin gateway. Embed tokens bind a workspace, published Agent Version, allowed origin, actions, and expiry.

See [ADR-0001](adr-0001-runtime-extension-contracts.md) for the contract decisions implemented by the prototype.

## Remaining Decisions

- Checkpoint storage and resume semantics
- Production PostgreSQL, Redis, Qdrant, and object-storage adapters
- Team membership and RBAC beyond workspace ownership
- Signed extension packaging and distribution trust
- Evaluation and observability integration
