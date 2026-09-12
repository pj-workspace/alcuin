# Release readiness

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
