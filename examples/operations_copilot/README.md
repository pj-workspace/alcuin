# Operations Copilot example

Operations Copilot is an explicit domain example, not part of Alcuin Core. It proves that a
published Agent, a built-in Extension adapter, declarative UI blocks, approvals, and an embedded
host can share one versioned Agent Definition.

Run the example API from the repository root:

```bash
uv run --project apps/api uvicorn examples.operations_copilot.app:app --reload --port 8000
```

The normal `alcuin_api.main:app` starts with only the domain-neutral **Alcuin Starter** and does
not register the Operations adapter.
