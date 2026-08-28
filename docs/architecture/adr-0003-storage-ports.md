# ADR-0003: PostgreSQL-only storage behind Repository ports

- Status: Accepted
- Date: 2026-08-28

## Context

The original pre-alpha API, runtime, knowledge service, and Extension executor depended directly
on one SQLite `Store`. That implementation was useful for proving contracts but did not match the
deployment target and leaked driver failures into the HTTP layer.

## Decision

`alcuin-storage` owns structural persistence ports and one PostgreSQL implementation. Consumers depend on the
narrowest relevant port:

- `RuntimeRepository` for Run state, ordered events, approvals, threads, and frozen versions;
- `ExtensionRepository` for Workspace-owned Extension lifecycle and resolution;
- `KnowledgeRepository` for source and document lifecycle metadata;
- `ControlPlaneRepository` for the API composition root.

`PostgresStore` uses a bounded psycopg connection pool. Every lookup and mutation carries an
explicit `workspace_id`; no ambient global Workspace is accepted. The API constructs the Store
from one required PostgreSQL URL and injects ports into services. Alembic is the only schema
migration path. The SQLite implementation and configuration path are removed after contract
parity is verified.

Each Run owns a persisted `next_event_sequence` cursor. The adapter increments that cursor and
inserts the event in the same PostgreSQL transaction instead of calculating
`MAX(sequence) + 1`.

## Verification gate

The package contract suite runs on PostgreSQL 17 and proves that the Store satisfies the complete
control-plane port, that independent Store instances can concurrently append an ordered event sequence without gaps or
duplicates, and that one Workspace cannot read another Workspace's Agent, Thread, Run, Event,
Approval, Extension, credential reference, or knowledge source. Architecture tests reject application,
runtime-framework, vector-database, and domain-Extension imports from the storage package.

## Consequences

There is no second database behavior to maintain and no silent local fallback when PostgreSQL is
unavailable or unmigrated. Development and tests require the Compose service. Redis-backed queues,
high-availability topology, and general load limits remain separate decisions and claims.
