# ADR-0002: Modular package boundaries

- Status: Accepted
- Date: 2026-08-28

## Context

The pre-alpha proved the end-to-end Agent, tool, knowledge, Extension, approval, and Embed
flows inside two applications. Public contracts, runtime code, storage, connectors, HTTP
routes, domain examples, and web protocol calls nevertheless accumulated inside application
source trees. Moving directly to PostgreSQL or adding more domain features at that point would
increase coupling and make reuse by external hosts harder.

## Decision

Alcuin uses an inward dependency direction:

```text
Applications and examples
          ↓
Runtime · Storage · Connectors · SDKs
          ↓
Framework-neutral Core contracts
```

The first extraction establishes:

- `alcuin-core` for Python Agent, Extension, event, policy, Embed, and tool envelopes;
- `@alcuin/sdk` for configurable REST and resumable SSE access;
- web `app`, `features`, and `shared` layers;
- `extensions/operations-copilot` as an explicit domain package.

`apps/api` remains the composition root. Runtime and connector modules are still implemented
there and will move in separate behavior-preserving slices. Storage ports, Alembic migrations,
and the single PostgreSQL implementation have since moved to `alcuin-storage` under ADR-0003. A directory name alone
does not establish a package boundary: extracted packages require their own manifest, tests,
public imports, and dependency checks.

## Enforced rules

- Core does not import FastAPI, LangGraph, infrastructure clients, applications, or Extensions.
- Domain Extensions depend on Core contracts, never on the API application.
- `@alcuin/sdk` receives its base URL and Workspace explicitly and never reads Next.js state.
- Web shared modules do not import features; features do not import route modules.
- Applications compose packages; packages do not reach back into applications.

## Consequences

The same public contracts and SDK can be used by Studio, Embed, tests, and external hosts.
PostgreSQL, alternative runtimes, and additional connectors can be introduced behind explicit
ports rather than by branching inside HTTP routes. During migration, some compatibility imports
may remain inside `apps/api`, but new platform contracts must be added to Core first.
