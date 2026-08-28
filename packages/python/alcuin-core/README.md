# alcuin-core

Framework-neutral Python contracts for Agent Definitions, Extensions, execution events,
knowledge ingestion, approvals, and Embed Sessions.

This package must not depend on FastAPI, a model provider, a storage engine, or a domain
Extension. Application and adapter packages depend inward on `alcuin-core`.

From the repository root, run its isolated tests with:

```bash
uv run --project packages/python/alcuin-core pytest packages/python/alcuin-core/tests
```
