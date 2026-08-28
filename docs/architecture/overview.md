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

## Open Decisions

- Monorepo package layout
- Runtime event schema and persistence granularity
- Agent definition versioning model
- Extension packaging and trust policy
- Checkpoint storage and resume semantics
- Multi-tenant deployment boundary
- Evaluation and observability integration

These decisions will be resolved through architecture records during the foundation milestone.

