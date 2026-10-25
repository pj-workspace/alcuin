# Release readiness

## Canvas draft protection and consolidated delivery

The 2026-10-25 delivery closes ordinary Run stopping, user-facing work records,
saved-document continuation, and tab-local Canvas draft protection together.
The earlier sections below record the verification state of each preceding slice;
Git/PR history is the authority for subsequent integration, not those snapshots.

- Closing the editor keeps dirty text; a recovery banner and resource markers
  make it discoverable. Explicit discard requires confirmation.
- Reloading the same tab can restore its draft. Storage failures show a warning;
  no cloud autosave, encryption, cross-tab sync or post-tab-closure guarantee is
  claimed. Drafts are partitioned by Workspace/Thread/Artifact.
- Save conflicts retain local edits. Reviewing saved content is separate from
  choosing it or continuing with the draft. Continuing still requires Save.
- Recovery notices use 180ms entry motion; saved-content disclosure transitions
  open/closed where supported and respects reduced motion. Mobile controls keep
  44px targets; English and Chinese copy are supplied.

Local verification: workspace tests, type checks, lint and production builds
passed. Draft-specific pure suites cover 20 editor-model cases and seven storage
cases. Full desktop/mobile browser regression passed **110 cases, six conditional
skips** using installed Chrome and deterministic local providers. The added
journey covers close, reload, conflict review, explicit save and draft cleanup.
This is browser emulation, not real-phone keyboard or external-model acceptance.
No production deployment is included. Existing databases need migration `0014`.

Final backend rerun: **463 passed**, with 44 existing Starlette/Qdrant warnings.
After the full browser run, the draft journey was extended to confirm explicit
discard and capture settled disclosure screenshots. An initial desktop test
race clicked before Canvas hydration; its semantic wait was corrected. The final
focused desktop/mobile rerun passed **2/2**, including the theme-border cleanup.
No verification assertions or security checks were disabled.

## Saved-document continuation slice

This local slice targets `develop`; it is not committed, merged, or deployed by
this verification. It connects saved Canvas edits to a subsequent ordinary Chat
message without adding product-version management or changing the source file.

- “Use in conversation” attaches one saved document to the composer. The sent
  message keeps an inspectable, immutable snapshot, even after later Canvas edits.
- The server checks Workspace and Thread ownership and resolves the content;
  the client sends only its ID and expected saved version. Stale content returns
  a conflict, keeps the unsent message, refreshes the Canvas, and requires the
  user to attach the new content explicitly. Oversized references are rejected.
- Canonical message fields, including the content digest, arrive without a page
  reload or delaying the answer stream. A late response from another Thread
  cannot replace the current reference, clear its input, or connect its stream.
- Composer cards and native previews use scoped entry/exit motion, reduced-motion
  support, keyboard focus containment and focus restoration. Mobile controls
  retain 44px targets. HTML is literal text in the snapshot viewer, not executable.

Local verification on 2026-10-25: **463 Python tests passed**, plus workspace unit
and contract tests (including 28 SDK tests), type checks, lint and production
builds. The final full browser suite passed **108 cases with six device-conditional
skips**, including all 12 new desktop/mobile reference cases. This covers editing
and saving B, sending it, changing the original to C, and still reading B after
reload; conflict recovery; cross-Thread late responses; and actual motion/focus
behavior. Existing approvals, stop controls, naming, planning, streaming and
Artifact regression cases passed in the same run.

Browser verification used installed Chrome 155 with the repository's device
profiles because pinned Chromium was unavailable. Additional manual Ego browser
checks inspected the light-theme composer and preview at a 390px mobile width;
automated English/Chinese dark-theme screenshots were also inspected. Earlier
checks caught and corrected modal focus and cross-Thread input isolation issues;
test helpers now wait for semantic Canvas state rather than transient geometry.
Existing Starlette/Qdrant warnings remain, with no suppression added.

The tests use local deterministic providers and verify the exact content delivered
to the provider, not external-model answer quality or real-phone keyboard behavior.
The limit is one saved document of at most 100,000 UTF-8 bytes and a nonempty
instruction. Unsaved editor text, Task/Plan continuation, and Embed references
are not included. Original citation IDs stay scoped to their source Run; a
snapshot digest is content identity, not factual verification. This slice does
not add persistent per-Thread drafts: explicit navigation clears unsent input,
while a rejected send in the same Thread retains it. No new migration is needed
beyond the preceding cancellation slice's `0014`.

Verification commands (service-wrapped commands must run sequentially):

```sh
./scripts/with-test-services.sh uv run --project apps/api pytest -c apps/api/pyproject.toml
pnpm -r test
pnpm lint
pnpm build
./scripts/with-test-services.sh pnpm test:e2e:run --workers=1
```

The browser run used a temporary config selecting `channel: "chrome"` while
retaining the repository tests and desktop/mobile profiles.

## User-facing work records slice

This local slice targets `develop`; it is not committed, merged, or deployed by
this verification. It replaces the placeholder Runs dashboard with usable work
records, without expanding product-version management or embedding.

- Search and status filters work on the latest 30 Runs. The interface states this
  scope and no longer shows hardcoded success rates, durations, or token metrics.
- Each selected record loads its own verified snapshot. Slow old requests cannot
  replace the current result; failed requests have an explicit retry path.
- Results, citation inspection, deliverables, human decisions, and actual tool
  outcomes stay together. Permission is not presented as execution success;
  failed/stopped work is labelled incomplete and missing outcomes stay unknown.
- Open conversation returns to the original Thread. Markdown receipts include
  status, operation outcomes and source identifiers/locators, not private
  reasoning, answer transcripts, or raw tool arguments/results.
- Mobile uses list/detail navigation with restored focus and reading position.
  English/Chinese, light/dark themes, and reduced-motion preferences are covered;
  scoped entry motion uses 180ms and cleans up on navigation.

Local verification on 2026-10-25: workspace unit/contract tests, lint, type checks,
and production builds passed. The pure receipt model has 13 focused tests, and
the SDK suite has 27. The final full browser suite passed **96 cases with six
device-conditional skips**, including all ten new desktop/mobile receipt cases
and the prior stop, question, approval, streaming, and Artifact regressions.
Desktop light and mobile Chinese-dark screenshots were inspected. An earlier
empty-history fixture race was fixed by prefetching its bootstrap data and
returning static mock responses; the full suite was rerun after that correction.

Browser verification used installed Chrome 155 with the repository device
profiles because the pinned Chromium binary was unavailable. The checks use
deterministic model fixtures; mobile emulation does not certify physical-phone
keyboard behavior or external-provider latency. No backend behavior changed in
this records slice; the preceding stop slice's 430-test Python result remains
the backend verification. Existing databases still need its migration `0014`.

Verification commands (service-wrapped commands must run sequentially):

```sh
pnpm -r test
pnpm lint
pnpm build
./scripts/with-test-services.sh pnpm test:e2e:run --workers=1
```

The browser invocation used a temporary config selecting `channel: "chrome"`
without changing test assertions or device profiles. Records refresh manually;
history pagination, live record streaming, and a full transcript export are not
part of this slice.

## Studio stop-generation slice

This slice targets `develop`; it adds ordinary conversation controls, not
product-version management or production deployment.

- Stop records durable intent and waits for a real terminal receipt. Existing
  answer text, editable/downloadable Artifacts and citations remain available.
- Provider and read-only waits can be interrupted. A dispatched mutation is not
  rolled back: its actual receipt is retained before any later work is stopped.
- Pending questions and approvals close without inventing answers, permission
  decisions or successful tool results. Stale submissions are rejected.
- Unconfirmed network requests offer retry; an accepted stop can recover its
  terminal state after a lost event stream. Thread changes clean up requests.
- Stopped output is explicitly incomplete in future model context, including
  after compaction. Restart recovery does not replay uncertain actions.
- Sending/stopping use the same 44px control footprint. Both languages, narrow
  layouts and reduced-motion preferences retain the existing interaction model.

Apply migration `20261025_0014` before starting the API against an existing
database. Downgrade is blocked while cancellation requests or cancelled messages
remain; preserve their history rather than deleting it to bypass that guard.
The owner is still a single API worker. Task-owned Runs use Task controls; Embed
callers cannot use the ordinary stop endpoint. Stopping does not guarantee that
an external service has undone a dispatched action or stopped billing.

Local verification on 2026-10-25: 430 Python tests passed. Workspace unit and
contract tests, type checks, lint and production builds passed after the final
snapshot-timeout and cross-Thread isolation fixes. Existing Starlette deprecation
and Qdrant version-probe warnings remain; they are not newly suppressed.

The final full browser suite passed 86 cases with six device-conditional skips,
using installed Chrome 155 and the repository's desktop/mobile device profiles
(the pinned Playwright Chromium binary was unavailable). Stop-specific coverage
includes partial output across refresh and a subsequent turn, stale approval and
question rejection, and failed-stop retry in both profiles. Chinese and English
dark-mode screenshots were inspected; motion and reduced-motion checks passed.
Separate unit cases cover a hung snapshot and a late response after navigation.
These checks use deterministic provider fixtures; they do not certify external
model latency, production mutations or physical-phone keyboard behavior.

Verification commands (run service-wrapped commands sequentially):

```sh
./scripts/with-test-services.sh uv run --project apps/api pytest -c apps/api/pyproject.toml
pnpm -r test
pnpm lint
pnpm build
./scripts/with-test-services.sh pnpm test:e2e:run --workers=1
```

The browser run used a temporary config that retained these tests and device
profiles while selecting `channel: "chrome"`; no repository browser settings or
assertions were weakened to accommodate the missing pinned binary.

## Studio approval continuation slice

This slice targets `develop`, with no production deployment or new product-facing
version-management work.

- Ordinary Studio model-generated approvals resume the same model loop after
  approval or refusal, instead of ending with a synthetic operation receipt.
- The decision is saved and returned before background continuation finishes,
  allowing Studio to display streamed progress and restore it after refresh.
- Authorized tool results preserve source evidence for the model. Refusal does
  not execute the tool; remaining sibling calls must be requested again.
- Private checkpoints preserve the original model, attachments and execution
  budgets. Duplicate decisions cannot execute the operation again. Interrupted
  work fails closed for verification rather than replaying uncertain writes.
- Checkpoint preparation decodes tool JSON before redaction, including secret
  values containing quotes, newlines and backslashes.

Apply migration `20261025_0013` before starting the API against an existing
database. Resolve pending approval continuations before downgrading. Task,
Embed, legacy approvals and explicit one-off tool requests retain their previous
adapter/receipt behavior; this slice does not extend their suspension boundaries.

Local verification on 2026-10-25: 402 Python tests passed. Workspace tests, type
checks, lint and production builds passed. Existing Starlette deprecation and
Qdrant version-probe warnings remain; they are not newly suppressed.

Browser acceptance used installed Chrome 155 with the repository's desktop and
mobile device profiles; the pinned Playwright Chromium binary was unavailable.
The full suite initially passed 74 cases with six device-conditional skips; two
new approval gate cases exposed a fixture missing the final-answer delimiter.
After correcting that fixture without weakening the assertions, the focused
approval, clarification and Studio suite passed 26 cases with two conditional
skips, including both approval gate cases. The entire suite was not rerun after
that fixture-only correction. This does not certify physical-phone keyboard
behavior or latency from an external model.

Verification commands (run service-wrapped commands sequentially):

```sh
./scripts/with-test-services.sh uv run --project apps/api pytest -c apps/api/pyproject.toml
pnpm -r test
pnpm lint
pnpm build
./scripts/with-test-services.sh pnpm test:e2e:run --workers=1
./scripts/with-test-services.sh pnpm test:e2e:run tests/e2e/approval-continuation.spec.ts tests/e2e/approval-interaction.spec.ts tests/e2e/question-interaction.spec.ts tests/e2e/studio.spec.ts --workers=1
```

Manual acceptance: ask Studio to change a record, review the exact action, then
allow or decline it. The decision receipt should remain visible while the model
continues answering; refreshing must keep the same Run without repeating the
operation. These local checks use deterministic provider fixtures, not a live
external model or a production mutation target.

## Human interaction and mobile reading slice

This slice targets `develop`, with no production deployment or product-version
management changes.

- Studio can ask a necessary clarification, accept a choice or free-text answer,
  or continue after an explicit skip. Questions survive refresh; continuation
  stays in the same Run without replaying completed tools or resetting budgets.
- Approval cards distinguish permission from the actual operation result, retain
  decisions and reasons after refresh, and reject duplicate/stale decisions.
- Manual upward scrolling preserves the reading position while output arrives;
  Back to latest restores following. Hidden mobile panels retain their position.
- The mobile composer uses one toolbar row and a labelled settings panel for
  model, thinking effort, and context. Desktop controls stay inline.
- Inactive orbs and collapsed trace loops stop animating; active feedback and
  disclosure transitions remain, with reduced-motion and keyboard support.

Apply migration `20260922_0012` before starting the new API against an existing
database. Do not downgrade with unanswered questions. The checkpoint is private
runtime state, not a public event or a credential-entry mechanism.

Local verification on 2026-09-22: 388 Python tests passed (one existing Starlette
deprecation warning); the focused desktop/mobile suite below passed 30 cases
with four device-conditional skips. Workspace tests, type checks, lint, and
production builds passed. This is scoped acceptance, not a claim that every
browser test in the repository was rerun.

Verification commands (run service-wrapped commands sequentially):

```sh
pnpm test
pnpm -r test
pnpm -r lint
pnpm -r build
./scripts/with-test-services.sh pnpm test:e2e:run tests/e2e/studio.spec.ts tests/e2e/approval-interaction.spec.ts tests/e2e/question-interaction.spec.ts tests/e2e/mobile-composer.spec.ts tests/e2e/streaming-performance.spec.ts --workers=1
```

The browser suite uses an isolated database and deterministic provider fixtures;
it does not establish external-model latency or physical-phone keyboard quality.
Manual acceptance: ask for a report that needs clarification, answer or skip,
refresh the receipt, inspect an approval before allowing it, and scroll upward
during a long response. At 320/390px, open settings, change the model and thinking
effort, and check that the next request uses those values.

Known boundaries: Task/Embed questions and encrypted credential forms are not
implemented. Mutation approval executes the authorized adapter and records its
receipt, but does not resume a full multi-call model loop. Recovery after an
answer has been claimed fails closed rather than replaying uncertain work.

## Planning and streaming experience slice

This slice targets `develop`; it is not a production deployment or a new
product-facing version-management feature.

- Chat/Plan mode supports bounded, tool-free model proposals, editable steps,
  and explicit confirmation before durable Task execution.
- Task events retain the confirmed goal and step descriptions. Canonical
  Artifact refreshes preserve the current Canvas selection and open editor.
- Conversation names are generated asynchronously from accepted user text or
  Task goals, with bounded model calls, safe fallback, scoped claims, and
  protection against stale updates or overwriting custom names.
- Studio preserves ordered streamed output while avoiding unchanged Markdown
  reparsing and repeated synchronous height measurement. Citation anchors,
  thinking expansion motion, and manual upward scrolling remain usable.

Local verification for this slice:

- 370 Python tests passed, with one existing Starlette deprecation warning;
- the pre-optimization full browser suite passed 54 tests, with 4 conditional skips;
- after the rendering changes, focused Artifact, citation, naming, and streaming
  coverage passed 25 distinct browser cases, with 1 device-conditional skip
  (the two new interaction cases were rerun after correcting a test timing race);
- 86 Web unit tests, type checks, lint, and a production Web build passed;
- controlled three-sample before/after browser replays preserved all 3,200
  fragments and complete Markdown. Layout-count medians fell from 132 to 108;
  these local fixture measurements do not measure provider latency or promise
  a particular frame rate;
- local API health and Studio HTTP checks passed; development services remain
  running for manual exploration.

To verify locally, run `pnpm -r test`, `pnpm -r lint`, and
`./scripts/with-test-services.sh pnpm test:e2e:run tests/e2e/streaming-performance.spec.ts tests/e2e/artifact-studio.spec.ts tests/e2e/thread-title.spec.ts --workers=1`.
Manually send a long request, expand/collapse its trace, scroll upward during
output, and inspect a source reference. Planning should remain a draft until
explicit confirmation. Apply Alembic migration `20260912_0011` before running
the new naming endpoints against an existing database.

## Agent foundation

The current `develop` branch is ready to promote to `main` as a pre-alpha
foundation for the Studio-first agent product.

Implemented and verified capabilities include:

- persistent conversation context, Skills, Rules, attachments, and scoped plugin imports;
- durable task execution with pause, approval, retry, recovery, model selection, and reasoning controls;
- independent streaming Artifacts with sandboxed HTML preview and editable Markdown/Word export;
- Run-local source evidence across conversation, Canvas, history restoration, and portable exports;
- responsive Studio layouts, agent presence motion, and bilingual product copy.

Local release verification completed before promotion:

- 333 API and Python package tests passed;
- 29 browser tests passed and 3 intentionally skipped;
- workspace tests, lint, and production builds passed;
- API and Studio smoke checks passed on the merged `develop` branch.

This promotion does not deploy a production environment. Product-facing version
management and quick embedding remain outside the current product focus.
