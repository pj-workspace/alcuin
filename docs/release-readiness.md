# Release readiness

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
