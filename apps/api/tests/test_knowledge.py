from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from qdrant_client import models

from alcuin_api.config import Settings
from alcuin_core.contracts import (
    AgentDefinition,
    KnowledgeDocumentCreate,
    KnowledgeSourceCreate,
)
from alcuin_api.knowledge import (
    EmbeddingProviderError,
    HybridEmbedding,
    KnowledgeHit,
    KnowledgeService,
    QwenEmbeddingProvider,
    QdrantKnowledgeIndex,
    chunk_document,
)
from alcuin_api.main import create_app
from alcuin_api.store import Store
from alcuin_api.tools import ToolContext, ToolError
from alcuin_operations_copilot import seed_operations_demo


class FakeKnowledgeIndex:
    def __init__(self) -> None:
        self.index_revision = "test-v1"
        self.upserts: list[dict[str, Any]] = []
        self.searches: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.hits: list[KnowledgeHit] = []
        self.fail_upsert = False
        self.closed = False

    async def upsert_document(self, **kwargs: Any) -> None:
        if self.fail_upsert:
            raise RuntimeError("raw backend detail")
        self.upserts.append(kwargs)

    async def search(self, **kwargs: Any) -> list[KnowledgeHit]:
        self.searches.append(kwargs)
        return list(self.hits)

    async def delete_source(self, *, workspace_id: str, source_id: str) -> None:
        self.deleted.append((workspace_id, source_id))

    async def health(self) -> dict[str, Any]:
        return {"status": "healthy", "points_count": len(self.upserts)}

    async def aclose(self) -> None:
        self.closed = True


def add_workspace(store: Store, workspace_id: str) -> None:
    with store.connection:
        store.connection.execute(
            "INSERT INTO workspaces(id, name, created_at) VALUES (?, ?, ?)",
            (workspace_id, workspace_id, "2026-08-28T00:00:00+00:00"),
        )


def test_chunk_document_is_bounded_and_deterministic() -> None:
    content = "# 产品手册\n\n" + "知识库用于回答企业内部问题。" * 160 + "\n\n第二节说明审批流程。"
    first = chunk_document(content, max_chars=400, overlap_chars=60)
    second = chunk_document(content, max_chars=400, overlap_chars=60)

    assert first == second
    assert len(first) > 2
    assert [chunk.index for chunk in first] == list(range(len(first)))
    assert all(0 < len(chunk.content) <= 400 for chunk in first)


@pytest.mark.asyncio
async def test_ingestion_is_idempotent_and_failure_is_sanitized() -> None:
    store = Store(":memory:")
    index = FakeKnowledgeIndex()
    service = KnowledgeService(store, index)
    source = store.create_knowledge_source(
        "ws_demo",
        KnowledgeSourceCreate(name="Product handbook"),
    )
    payload = KnowledgeDocumentCreate(
        title="Agent platform",
        content="Alcuin supports governed knowledge retrieval.\n\nTools remain workspace scoped.",
        metadata={"department": "engineering"},
    )

    first, first_indexed = await service.ingest_document("ws_demo", source["id"], payload)
    second, second_indexed = await service.ingest_document("ws_demo", source["id"], payload)

    assert first_indexed is True
    assert second_indexed is False
    assert first["id"] == second["id"]
    assert first["status"] == "ready"
    assert "content" not in first
    assert len(index.upserts) == 1
    stored_content = store.connection.execute(
        "SELECT content FROM knowledge_documents WHERE id = ?",
        (first["id"],),
    ).fetchone()["content"]
    assert stored_content.startswith("Alcuin supports")

    index.index_revision = "test-v2"
    migrated_service = KnowledgeService(store, index)
    migrated, migrated_indexed = await migrated_service.ingest_document(
        "ws_demo", source["id"], payload
    )
    assert migrated_indexed is True
    assert migrated["id"] == first["id"]
    assert migrated["index_revision"] == "test-v2"
    assert len(index.upserts) == 2

    index.fail_upsert = True
    with pytest.raises(RuntimeError, match="raw backend detail"):
        await service.ingest_document(
            "ws_demo",
            source["id"],
            KnowledgeDocumentCreate(title="Failure", content="Different document content."),
        )
    failed = store.list_knowledge_documents("ws_demo", source["id"])[0]
    assert failed["status"] == "failed"
    assert failed["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_tool_intersects_agent_sources_with_workspace_ownership() -> None:
    store = Store(":memory:")
    add_workspace(store, "ws_other")
    owned = store.create_knowledge_source(
        "ws_demo", KnowledgeSourceCreate(name="Owned handbook")
    )
    foreign = store.create_knowledge_source(
        "ws_other", KnowledgeSourceCreate(name="Foreign handbook")
    )
    index = FakeKnowledgeIndex()
    index.hits = [
        KnowledgeHit(
            source_id=owned["id"],
            document_id="kdoc_owned",
            title="Owned document",
            chunk_index=0,
            content="Only workspace-owned content is visible.",
            score=0.75,
        )
    ]
    service = KnowledgeService(store, index)

    result = await service.execute(
        ToolContext(
            workspace_id="ws_demo",
            run_id="run_1",
            thread_context={},
            knowledge_source_ids=(owned["id"], foreign["id"]),
        ),
        {"query": "workspace content", "limit": 3},
    )

    assert index.searches[0]["source_ids"] == (owned["id"],)
    assert result.data["hits"][0]["content"].startswith("Only workspace")
    assert result.citations[0].locator == (
        f"knowledge://{owned['id']}/kdoc_owned#chunk-0"
    )

    with pytest.raises(ToolError) as error:
        await service.execute(
            ToolContext("ws_other", "run_2", {}, (owned["id"],)),
            {"query": "workspace content"},
        )
    assert error.value.code == "knowledge_not_configured"


class CapturingQdrantClient:
    def __init__(self) -> None:
        self.query_kwargs: dict[str, Any] = {}

    def query_points(self, **kwargs: Any) -> Any:
        self.query_kwargs = kwargs
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    score=0.5,
                    payload={
                        "workspace_id": "ws_demo",
                        "source_id": "ksrc_owned",
                        "document_id": "kdoc_1",
                        "title": "Scoped",
                        "chunk_index": 0,
                        "content": "Scoped result",
                        "metadata": {},
                    },
                )
            ]
        )


class IncompatibleQdrantClient:
    def collection_exists(self, name: str) -> bool:
        del name
        return True

    def get_collection(self, name: str) -> Any:
        del name
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors={"dense": SimpleNamespace(size=64)},
                    sparse_vectors={},
                )
            )
        )


class FakeEmbeddingProvider:
    model = "fake-hybrid"
    dimensions = 32

    async def embed_documents(self, texts: list[str]) -> list[HybridEmbedding]:
        return [
            HybridEmbedding(
                dense=[0.1] * self.dimensions,
                sparse=models.SparseVector(indices=[1], values=[1.0]),
            )
            for _ in texts
        ]

    async def embed_query(self, text: str) -> HybridEmbedding:
        del text
        return HybridEmbedding(
            dense=[0.1] * self.dimensions,
            sparse=models.SparseVector(indices=[1], values=[1.0]),
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_qdrant_hybrid_query_has_mandatory_scope_filter() -> None:
    client = CapturingQdrantClient()
    index = QdrantKnowledgeIndex(
        Settings(
            qdrant_url="http://qdrant.test",
            dashscope_api_key="test-key",
            knowledge_dense_dimensions=32,
        ),
        client=client,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(),
    )
    index._collection_ready = True

    hits = await index.search(
        workspace_id="ws_demo",
        source_ids=["ksrc_owned"],
        query="scoped query",
        limit=4,
    )

    query_filter = client.query_kwargs["query_filter"]
    conditions = {condition.key: condition.match for condition in query_filter.must}
    assert conditions["workspace_id"].value == "ws_demo"
    assert conditions["source_id"].any == ["ksrc_owned"]
    assert len(client.query_kwargs["prefetch"]) == 2
    assert isinstance(client.query_kwargs["query"], models.FusionQuery)
    assert hits[0].content == "Scoped result"


def test_qdrant_rejects_an_incompatible_existing_collection() -> None:
    index = QdrantKnowledgeIndex(
        Settings(
            qdrant_url="http://qdrant.test",
            dashscope_api_key="test-key",
            knowledge_dense_dimensions=1_024,
        ),
        client=IncompatibleQdrantClient(),  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(),
    )

    with pytest.raises(RuntimeError, match="incompatible"):
        index._ensure_collection_sync()


def qwen_response(texts: list[str], dimensions: int = 32) -> dict[str, Any]:
    return {
        "output": {
            "embeddings": [
                {
                    "text_index": index,
                    "embedding": [float(index + 1)] * dimensions,
                    "sparse_embedding": [
                        {"index": index + 10, "value": 0.5, "token": text[:8]}
                    ],
                }
                for index, text in reversed(list(enumerate(texts)))
            ]
        }
    }


@pytest.mark.asyncio
async def test_qwen_embedding_batches_and_preserves_provider_order() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        texts = body["input"]["texts"]
        return httpx.Response(200, json=qwen_response(texts), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QwenEmbeddingProvider(
            Settings(
                dashscope_api_key="secret-test-key",
                dashscope_http_api_url="https://dashscope.test/api/v1",
                knowledge_dense_dimensions=32,
            ),
            client=client,
        )
        rows = await provider.embed_documents([f"document {index}" for index in range(11)])
        query = await provider.embed_query("query")

    assert [len(request["input"]["texts"]) for request in requests] == [10, 1, 1]
    assert [request["parameters"]["text_type"] for request in requests] == [
        "document",
        "document",
        "query",
    ]
    assert all(request["parameters"]["output_type"] == "dense&sparse" for request in requests)
    assert rows[0].dense[0] == 1.0
    assert rows[9].dense[0] == 10.0
    assert query.sparse.indices == [10]


@pytest.mark.asyncio
async def test_qwen_embedding_retries_transient_errors_and_redacts_failures() -> None:
    calls = 0

    async def transient_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, text="secret provider quota detail", request=request)
        return httpx.Response(200, json=qwen_response(["query"]), request=request)

    settings = Settings(
        dashscope_api_key="secret-test-key",
        dashscope_http_api_url="https://dashscope.test/api/v1",
        knowledge_dense_dimensions=32,
        embedding_max_retries=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transient_handler)) as client:
        provider = QwenEmbeddingProvider(settings, client=client)
        result = await provider.embed_query("query")
    assert calls == 2
    assert len(result.dense) == 32

    async def rejected_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            text="secret provider body and secret-test-key",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(rejected_handler)) as client:
        provider = QwenEmbeddingProvider(settings, client=client)
        with pytest.raises(EmbeddingProviderError) as error:
            await provider.embed_query("query")
    assert str(error.value) == "embedding request rejected"
    assert "secret" not in str(error.value)


def test_knowledge_api_is_scoped_idempotent_and_reference_safe() -> None:
    store = Store(":memory:")
    seed_operations_demo(store)
    add_workspace(store, "ws_other")
    index = FakeKnowledgeIndex()
    service = KnowledgeService(store, index)
    app = create_app(
        Settings(database_path=":memory:", searxng_url="", qdrant_url=""),
        store=store,
        knowledge_service=service,
    )
    demo_headers = {"X-Alcuin-Workspace": "ws_demo"}
    other_headers = {"X-Alcuin-Workspace": "ws_other"}

    with TestClient(app) as client:
        source_response = client.post(
            "/v1/knowledge/sources",
            headers=demo_headers,
            json={"name": "Operations runbooks", "description": "Approved procedures"},
        )
        assert source_response.status_code == 201
        source = source_response.json()

        first = client.post(
            f"/v1/knowledge/sources/{source['id']}/documents",
            headers=demo_headers,
            json={"title": "Checkout", "content": "Escalate checkout incidents to on-call."},
        )
        duplicate = client.post(
            f"/v1/knowledge/sources/{source['id']}/documents",
            headers=demo_headers,
            json={"title": "Checkout", "content": "Escalate checkout incidents to on-call."},
        )
        assert first.status_code == 201
        assert duplicate.status_code == 200
        assert duplicate.json()["indexed"] is False
        assert len(index.upserts) == 1

        assert (
            client.get(
                f"/v1/knowledge/sources/{source['id']}/documents",
                headers=other_headers,
            ).status_code
            == 404
        )

        agent = client.get("/v1/agents/agt_operations", headers=demo_headers).json()
        definition = AgentDefinition.model_validate(agent["definition"]).model_dump(mode="json")
        definition["tools"] = [*definition["tools"], "knowledge.search"]
        no_source_definition = dict(definition)
        no_source_definition["knowledge"] = []
        draft_without_source = client.post(
            "/v1/agents/agt_operations/versions",
            headers=demo_headers,
            json={"definition": no_source_definition},
        )
        assert draft_without_source.status_code == 201
        assert (
            client.post(
                "/v1/agents/agt_operations/publish",
                headers=demo_headers,
            ).status_code
            == 409
        )

        definition["knowledge"] = [source["id"]]
        version = client.post(
            "/v1/agents/agt_operations/versions",
            headers=demo_headers,
            json={"definition": definition},
        )
        assert version.status_code == 201

        referenced_delete = client.delete(
            f"/v1/knowledge/sources/{source['id']}",
            headers=demo_headers,
        )
        assert referenced_delete.status_code == 409
        assert referenced_delete.json()["detail"]["references"][0]["agent_id"] == (
            "agt_operations"
        )

        foreign_definition = dict(definition)
        foreign_definition["knowledge"] = ["ksrc_not_owned"]
        rejected = client.post(
            "/v1/agents/agt_operations/versions",
            headers=demo_headers,
            json={"definition": foreign_definition},
        )
        assert rejected.status_code == 422
