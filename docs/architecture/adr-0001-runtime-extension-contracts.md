# ADR-0001: Runtime, Extension, and Embed Contracts

- Status: Accepted for pre-alpha
- Date: 2026-08-28

## Context

Alcuin must run the same Agent Definition in its own Studio and inside another product. Runtimes, model providers, tools, and integration protocols will evolve independently, so none of them can define the public product contract.

## Decision

1. An Agent Definition is declarative and versioned. Publishing exposes an immutable version to new Embed Sessions.
2. Every runtime consumes a normalized request and emits ordered `ExecutionEvent` values. LangGraph is an adapter, not an API dependency.
3. Threads and Runs are separate resources. Events are persisted by sequence and can be resumed through SSE using `Last-Event-ID` or `after`.
4. Extensions use `alcuin.extension.json`. Installation is disabled-first and permissions are inspected before activation.
5. MCP stdio, SSE, and Streamable HTTP terminate at the backend gateway. OpenAPI operations are normalized into the same tool vocabulary.
6. Mutating operations are denied from direct extension invocation and must pass through an approval-gated Agent Run.
7. Credentials are referenced through `secret://` identifiers. Definitions and events never store raw credential values.
8. Embed Sessions are short-lived HMAC-signed claims bound to workspace, published Agent Version, origin, expiry, and allowed actions.
9. Extension UI contributions are declarative data blocks. The web application does not execute third-party React code.

## Consequences

- Provider and runtime adapters can be replaced without changing Studio or Embed clients.
- A disconnected SSE client can recover ordered events without replaying a Run.
- Domain-specific applications can ship as extensions rather than forks of the platform core.
- The pre-alpha requires an explicit migration before introducing a distributed event bus or production multi-region persistence.
