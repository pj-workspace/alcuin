# alcuin-storage

Workspace-scoped persistence ports and the PostgreSQL implementation for Alcuin.

Services depend on the narrow `RuntimeRepository`, `ExtensionRepository`, or
`KnowledgeRepository` protocols. `apps/api` is the composition root and creates the pooled
`PostgresStore`; runtime, knowledge, and Extension services never access the driver directly.

Alembic owns schema changes. PostgreSQL is the only control-plane database implementation; tests
run against an isolated Compose project and temporary volume.

```bash
./scripts/with-test-services.sh \
  uv run --project apps/api pytest packages/python/alcuin-storage/tests
```
