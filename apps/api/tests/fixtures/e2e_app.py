from __future__ import annotations

from typing import Any

import psycopg
from alcuin_knowledge import KnowledgeChunk, KnowledgeHit, KnowledgeService
from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_storage import PostgresStore
from alcuin_operations_copilot import operations_demo_adapter, seed_operations_demo


class MemoryKnowledgeIndex:
    """Deterministic browser-test index; production continues to use Qdrant and Qwen."""

    index_revision = "e2e-memory-v1"

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def upsert_document(
        self,
        *,
        workspace_id: str,
        source_id: str,
        document_id: str,
        title: str,
        source_uri: str | None,
        metadata: dict[str, Any],
        chunks: tuple[KnowledgeChunk, ...],
    ) -> None:
        self.rows = [row for row in self.rows if row["document_id"] != document_id]
        self.rows.extend(
            {
                "workspace_id": workspace_id,
                "source_id": source_id,
                "document_id": document_id,
                "title": title,
                "source_uri": source_uri,
                "metadata": metadata,
                "chunk": chunk,
            }
            for chunk in chunks
        )

    async def search(
        self,
        *,
        workspace_id: str,
        source_ids: tuple[str, ...],
        query: str,
        limit: int,
    ) -> list[KnowledgeHit]:
        terms = {term.casefold() for term in query.split() if term}
        candidates = [
            row
            for row in self.rows
            if row["workspace_id"] == workspace_id and row["source_id"] in source_ids
        ]
        candidates.sort(
            key=lambda row: sum(
                term in row["chunk"].content.casefold() for term in terms
            ),
            reverse=True,
        )
        return [
            KnowledgeHit(
                source_id=row["source_id"],
                document_id=row["document_id"],
                title=row["title"],
                chunk_index=row["chunk"].index,
                content=row["chunk"].content,
                score=1.0,
                source_uri=row["source_uri"],
                metadata=row["metadata"],
            )
            for row in candidates[:limit]
        ]

    async def delete_source(self, *, workspace_id: str, source_id: str) -> None:
        self.rows = [
            row
            for row in self.rows
            if not (
                row["workspace_id"] == workspace_id and row["source_id"] == source_id
            )
        ]

    async def health(self) -> dict[str, Any]:
        return {"status": "healthy", "points_count": len(self.rows)}

    async def aclose(self) -> None:
        return None


settings = Settings()
with psycopg.connect(settings.database_url) as connection:
    connection.execute(
        """TRUNCATE TABLE
        knowledge_documents, knowledge_sources, extensions, approvals, events,
        runs, threads, agent_versions, agents, workspaces
        CASCADE"""
    )
store = PostgresStore(settings.database_url)
seed_operations_demo(store)
knowledge_service = KnowledgeService(store, MemoryKnowledgeIndex())
app = create_app(
    settings,
    store=store,
    knowledge_service=knowledge_service,
    builtin_adapters={"operations-demo": operations_demo_adapter},
)
