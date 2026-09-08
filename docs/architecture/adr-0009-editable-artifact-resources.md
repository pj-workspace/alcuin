# ADR-0009: Persisted editable Artifact resources

- Status: Accepted
- Date: 2026-08-29

The original single-Artifact/event-projection limitations below are superseded by
[ADR-0010](adr-0010-independent-artifacts-and-evidence.md). Atomic persistence and
optimistic-edit invariants remain in force; product-facing version history remains paused.

## Context

The first Studio prototype rendered `artifact.updated` events as a convenient projection of the
assistant response. That made the Canvas feel live, but the document itself was not a resource: a
refresh could only rediscover the event payload, user edits had nowhere authoritative to persist,
and two open clients could silently overwrite each other. Treating an Artifact as a chat fragment
also prevented future task checkpoints from referring to a stable document.

Alcuin does not expose product-facing version management in this phase. It still needs an internal
revision boundary so saves are safe, runtime output is auditable, and stale clients fail instead of
losing work.

## Decision

### An Artifact is a Workspace-owned resource

Each generated Artifact belongs to one Workspace and Thread and records the Run that first created
it. A Run owns at most one Artifact in this slice. The public projection includes its stable id,
title, kind, content type, current content, an optimistic concurrency token, and timestamps.

The Studio may display assistant deltas as an ephemeral live projection while a Run is active. On
`artifact.updated`, the API replaces that projection with the canonical persisted resource. After a
refresh, the latest resource is read directly rather than reconstructed from an older event.

### Runtime persistence and the Run event are atomic

The runtime does not append an arbitrary Artifact payload directly to the event log. The storage
port validates and persists the canonical Artifact, records an immutable internal revision when its
content changes, and appends the corresponding `artifact.updated` Run event in the same database
transaction. The event contains the safe public projection and therefore cannot point at a document
that failed to persist.

If a runtime retries an unchanged Artifact snapshot, storage returns the existing canonical event
without allocating another Artifact revision or Run-event sequence. Once a Run has a durable
terminal event, later runtime Artifact emissions are rejected and the terminal event remains last.

Provider-supplied ids and revision numbers are ignored. Resource identity, Workspace ownership,
revision numbers, and timestamps are assigned by Alcuin.

### User edits use optimistic concurrency

`PATCH /v1/artifacts/{artifact_id}` requires the revision observed by the editor. A matching save
creates a new immutable internal revision and advances the latest projection. A stale save returns
`409 Conflict`; the client keeps the local draft and offers to load the latest server document.

The internal revisions are an integrity and recovery mechanism, not a visible version-history
feature. This slice does not expose revision listing, diff, rollback, branching, or publish flows.

### Content is deliberately constrained

The supported content types are Markdown, plain text, and syntactically valid JSON, with a
500,000-character limit.
Titles and kinds are validated before persistence. Artifact content is untrusted presentation data:
the web client renders Markdown through the existing safe renderer and never executes embedded
HTML or third-party code.

## Consequences

- A generated document survives reloads and process restarts independently of the Run event log.
- Workspace filters and composite foreign keys protect Artifact reads, edits, revisions, and Run
  relationships.
- Stale editors cannot silently overwrite newer work.
- Streaming remains responsive because the Canvas can project deltas before canonical persistence.
- The single-Artifact-per-Run rule is intentionally narrow; multiple named outputs, binary
  Artifacts, revision history UI, collaboration, and publish lifecycle require later contracts.

## Studio interaction and motion

- Canvas opening, Preview/Edit transitions, save progress, saved confirmation, and conflict state
  changes use state-driven 140–220 ms opacity, transform, or height transitions.
- Motion communicates real persistence state; it is not a fake progress animation.
- `prefers-reduced-motion` removes non-essential movement while preserving state labels and focus.
- The editor is disabled while the source Run is still streaming, so an ephemeral projection cannot
  be mistaken for a saved editable document.

## Verification

- Repository tests cover atomic event persistence and rollback, immutable revisions, replay no-ops,
  stale revision conflicts, concurrent event sequencing, terminal guards, and Workspace isolation.
- API tests cover Thread ownership, list/get/edit authorization, validation, and `409 Conflict`.
- Runtime tests confirm every `artifact.updated` path uses the canonical persistence operation.
- Studio tests cover live-to-persisted handoff, reload hydration, edit/save/cancel/conflict states,
  state-driven motion classes, and reduced-motion behavior.
