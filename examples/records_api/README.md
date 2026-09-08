# Records API demo

This domain-neutral FastAPI service is the live OpenAPI fixture used in the
Alcuin acceptance flow. It exposes one read operation and one mutating
operation so the Extension Center can demonstrate operation selection and
approval-gated writes.

From the repository root:

```bash
uv run --project apps/api uvicorn examples.records_api.app:app --port 9411
```

Import `http://127.0.0.1:9411/openapi.json` from the Extension Center. Private
network imports must remain an explicit local-development opt-in.
