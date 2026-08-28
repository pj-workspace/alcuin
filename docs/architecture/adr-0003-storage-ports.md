# ADR-0003: Storage ports before production adapters

- Status: Accepted
- Date: 2026-08-28

## Context

The pre-alpha API, runtime, knowledge service, and Extension executor all depended directly on a
single SQLite `Store`. Introducing PostgreSQL at that boundary would require editing execution
and domain-service code at the same time as changing persistence semantics, making failures hard
to isolate.

## Decision

`alcuin-storage` owns structural persistence ports and concrete adapters. Consumers depend on the
narrowest relevant port:

- `RuntimeRepository` for Run state, ordered events, approvals, threads, and frozen versions;
- `ExtensionRepository` for Workspace-owned Extension lifecycle and resolution;
- `KnowledgeRepository` for source and document lifecycle metadata;
- `ControlPlaneRepository` for the API composition root.

The existing behavior-preserving implementation is named `SqliteStore`. Every lookup and
mutation carries an explicit `workspace_id`; no ambient global Workspace is accepted. The API
constructs the adapter and injects ports into services.

Each Run owns a persisted `next_event_sequence` cursor. The adapter increments that cursor and
inserts the event in the same transaction instead of calculating `MAX(sequence) + 1`; existing
databases backfill the cursor from persisted events when opened. SQLite uses WAL mode and a
bounded busy timeout for independent local connections.

## Verification gate

The package contract suite proves that the adapter satisfies the complete control-plane port,
that independent connections can concurrently append an ordered event sequence without gaps or
duplicates, and that one Workspace cannot read another Workspace's Agent, Thread, Run, Event,
Approval, Extension, credential reference, or knowledge source. Architecture tests reject application,
runtime-framework, vector-database, and domain-Extension imports from the storage package.

## Consequences

PostgreSQL can be added as another adapter and compared against the same contract suite before
the API selects it. This decision does not claim production migrations, connection pooling,
multi-host storage, or general concurrent-write guarantees beyond the verified event append;
those belong to the next adapter slice and must be verified independently.
