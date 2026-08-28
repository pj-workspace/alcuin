# alcuin-storage

Workspace-scoped persistence ports and the current SQLite adapter for Alcuin.

Services depend on the narrow `RuntimeRepository`, `ExtensionRepository`, or
`KnowledgeRepository` protocols. `apps/api` is the composition root and creates the concrete
`SqliteStore`. A PostgreSQL adapter can therefore be introduced without branching inside the
runtime, knowledge, or Extension services.

The SQLite adapter preserves the pre-alpha local bootstrap and is not presented as the final
production migration system.

```bash
uv run --project apps/api pytest packages/python/alcuin-storage/tests
```
