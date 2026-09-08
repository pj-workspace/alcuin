# ADR-0006: Conversation and Context Kernel

- Status: Accepted
- Date: 2026-08-29

## Context

The pre-alpha stored Threads and Runs, but a Run sent only its current prompt to the model. Studio
also created a new Thread for every submission. Agent Instructions were present, while conversation
history, durable Messages, token budgets, compaction provenance, and an operator-visible account of
effective context were absent.

This made the product look conversational without providing a real continuous Agent session. It
also left Rules, Skills, attachments, tasks, and future runtime adapters without a stable context
boundary.

## Decision

### Messages are immutable conversation records

Every accepted Run atomically creates a Workspace-owned user Message with a Thread-local sequence.
Visible assistant output is accumulated only from `message.delta` events and finalized exactly once
as an assistant Message. Reasoning, tool calls, approvals, and execution diagnostics remain
`ExecutionEvent` records and never become assistant chat content.

Raw Messages are never overwritten by compaction. Persisted attachment parts contain resource
references and metadata only; inline data URLs remain request-scoped.

### Thread and Run sequences remain separate

A Thread-local Message sequence orders durable conversation history. A Run-local event sequence
continues to be the canonical SSE cursor. These counters are never reused or projected onto each
other.

Only one active Run is allowed per Thread in this version. This gives follow-up messages,
approvals, and compaction an unambiguous order. Steering and queued follow-ups require a future
explicit inbox contract.

On single-instance startup, the API terminalizes persisted `queued` and `running` Runs as
`runtime_interrupted`. It preserves only visible `message.delta` output as an explicitly failed
assistant Message, appends one idempotent `run.failed` event, and never retries external tool work
automatically. Runs paused in `waiting_for_approval` remain resumable and are not terminalized.

### Context assembly is a provider-neutral package

`alcuin-context` owns deterministic layer ordering, normalized conversation messages, replaceable
token estimation, hard input budgets, and traceable compaction contracts. It does not import
FastAPI, provider SDKs, storage adapters, runtime frameworks, or domain extensions.

The composition order is:

1. Platform runtime and safety protocol
2. Workspace Rules
3. User Preferences
4. immutable Agent Instructions
5. Thread Rules
6. active Skills
7. compacted conversation summary
8. recent Messages
9. authorized, untrusted host context
10. the current user Message and request-scoped attachments

Not every resource in this order is implemented by this slice. Missing layers remain explicit
extension points rather than being simulated in one untyped system prompt.

Provider adapters receive one `ContextAssembly`. Chat Completions serializes its system prompt and
ordered Messages; Responses serializes the same normalized envelope. Provider-specific code must
not rebuild layer precedence.

### Every Run has an auditable context snapshot

The application persists one immutable context assembly per Run. Its trace records source identity,
version or sequence, token estimate, and digest. Operator APIs return this safe trace and budget
metadata, not raw model-visible content or credentials.

Host context is allow-listed by the immutable Agent version, size bounded, marked as untrusted data,
and redacted before it can enter the persisted snapshot.

### Compaction is an overlay

A compaction summarizes an exact complete-turn Message prefix and records its source Message ids,
digest, strategy, token estimates, and parent compaction. The provider sees the summary plus the
recent tail; Messages APIs continue to return the full original history.

Token counts are estimates until a model-specific tokenizer is registered. They are never reported
as provider billing usage.

### Studio is the primary product surface

Studio owns explicit Thread selection, restores it through the URL and local preference, lazily
creates a Thread only for the first submission, and reuses it for later Runs. Streaming state is
reduced into a multi-turn timeline with replay suppression. Motion is restrained to shared
140–220 ms tokens and has a static reduced-motion path.

The earlier third-party quick-embed direction is frozen. Existing compatibility code is not
expanded by this kernel and is not a driver for its public contracts.

## Consequences

- Skills, Rules, Model Profiles, attachments, live Artifacts, and Tasks can depend on one stable
  conversation/context boundary.
- A failed Run may retain an explicitly safe partial assistant Message while keeping its failed
  status.
- Refreshing Studio can reconstruct conversation content independently of ephemeral SSE buffers.
- PostgreSQL migrations must preserve Workspace filters and backfill compatible pre-kernel history.
- Destructive integration fixtures refuse database names that do not end in `_test`; a developer
  database is never an acceptable test reset target.
- Context overflow now fails closed when no complete Message prefix can be compacted.
- Content storage grows monotonically; retention and archival require a separate policy.

## Verification

- Contract tests keep Python and TypeScript event vocabulary aligned.
- PostgreSQL tests cover atomic Run/Message creation, active-Run exclusion, sequence allocation,
  idempotent finalization, Workspace isolation, inline-data rejection, compaction provenance, and
  immutable context snapshots.
- Runtime tests verify that a second Run sees the first user/assistant turn, Agent Instructions occur
  once, separate Threads cannot leak history, and persisted assistant content equals visible deltas.
- Startup-recovery tests verify idempotent interrupted-Run terminalization, safe partial output,
  approval preservation, Workspace isolation, and release of the Thread active-Run constraint.
- Studio tests cover Thread reuse, refresh hydration, replay suppression, active-Run recovery,
  attachments, Artifact continuity, and reduced-motion behavior.
