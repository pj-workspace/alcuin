# Release readiness

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
