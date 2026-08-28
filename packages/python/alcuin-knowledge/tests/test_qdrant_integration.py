from __future__ import annotations

import os
import uuid

import pytest
from qdrant_client import QdrantClient, models

from alcuin_knowledge import (
    HybridEmbedding,
    KnowledgeChunk,
    KnowledgeConfig,
    QdrantKnowledgeIndex,
)


class DeterministicEmbeddingProvider:
    model = "deterministic-test"
    dimensions = 32

    async def embed_documents(self, texts: list[str]) -> list[HybridEmbedding]:
        return [self._embedding(text) for text in texts]

    async def embed_query(self, text: str) -> HybridEmbedding:
        return self._embedding(text)

    async def aclose(self) -> None:
        return None

    def _embedding(self, text: str) -> HybridEmbedding:
        normalized = text.casefold()
        signal = 1.0 if "tungsten" in normalized else 0.25
        return HybridEmbedding(
            dense=[signal, 1.0 - signal, *([0.0] * 30)],
            sparse=models.SparseVector(
                indices=[1 if signal == 1.0 else 2],
                values=[1.0],
            ),
        )


@pytest.mark.asyncio
async def test_real_qdrant_enforces_workspace_scope_and_source_deletion() -> None:
    qdrant_url = os.getenv("ALCUIN_TEST_QDRANT_URL")
    if not qdrant_url:
        pytest.skip("ALCUIN_TEST_QDRANT_URL is not configured")

    collection = f"alcuin_test_{uuid.uuid4().hex}"
    config = KnowledgeConfig(
        qdrant_url=qdrant_url,
        dashscope_api_key="test-only",
        collection=collection,
        dense_dimensions=32,
        index_revision="integration-v1",
    )
    index = QdrantKnowledgeIndex(
        config,
        embedding_provider=DeterministicEmbeddingProvider(),
    )
    cleanup = QdrantClient(url=qdrant_url)
    try:
        await index.upsert_document(
            workspace_id="ws_alpha",
            source_id="source_alpha",
            document_id="document_alpha",
            title="Alpha tungsten policy",
            source_uri="upload://alpha.pdf",
            metadata={"workspace": "alpha"},
            chunks=[
                KnowledgeChunk(index=0, content="Tungsten risk is elevated."),
                KnowledgeChunk(index=1, content="Legacy mitigation is required."),
            ],
        )
        await index.upsert_document(
            workspace_id="ws_beta",
            source_id="source_beta",
            document_id="document_beta",
            title="Beta private policy",
            source_uri="upload://beta.pdf",
            metadata={"workspace": "beta"},
            chunks=[KnowledgeChunk(index=0, content="Tungsten risk is private.")],
        )
        await index.upsert_document(
            workspace_id="ws_alpha",
            source_id="source_alpha",
            document_id="document_alpha",
            title="Alpha tungsten policy",
            source_uri="upload://alpha.pdf",
            metadata={"workspace": "alpha", "revision": 2},
            chunks=[KnowledgeChunk(index=0, content="Tungsten risk is now moderate.")],
        )

        alpha_points = cleanup.count(
            collection_name=collection,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="workspace_id",
                        match=models.MatchValue(value="ws_alpha"),
                    ),
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value="document_alpha"),
                    ),
                ]
            ),
            exact=True,
        )

        alpha_hits = await index.search(
            workspace_id="ws_alpha",
            source_ids=["source_alpha", "source_beta"],
            query="tungsten risk",
            limit=5,
        )
        beta_from_alpha_source = await index.search(
            workspace_id="ws_beta",
            source_ids=["source_alpha"],
            query="tungsten risk",
            limit=5,
        )

        assert [hit.document_id for hit in alpha_hits] == ["document_alpha"]
        assert alpha_points.count == 1
        assert alpha_hits[0].content == "Tungsten risk is now moderate."
        assert beta_from_alpha_source == []
        assert alpha_hits[0].locator == (
            "knowledge://source_alpha/document_alpha#chunk-0"
        )

        await index.delete_source(
            workspace_id="ws_alpha",
            source_id="source_alpha",
        )
        assert await index.search(
            workspace_id="ws_alpha",
            source_ids=["source_alpha"],
            query="tungsten risk",
            limit=5,
        ) == []
        assert len(
            await index.search(
                workspace_id="ws_beta",
                source_ids=["source_beta"],
                query="tungsten risk",
                limit=5,
            )
        ) == 1
    finally:
        await index.aclose()
        if cleanup.collection_exists(collection):
            cleanup.delete_collection(collection)
        cleanup.close()
