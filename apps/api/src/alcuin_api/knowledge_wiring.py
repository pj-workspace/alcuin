from __future__ import annotations

from alcuin_knowledge import DocumentLimits, KnowledgeConfig, KnowledgeService

from .config import Settings
from .tools import ToolDefinition


def knowledge_config(settings: Settings) -> KnowledgeConfig:
    return KnowledgeConfig(
        qdrant_url=str(settings.qdrant_url or ""),
        qdrant_api_key=settings.qdrant_api_key,
        qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
        dashscope_api_key=settings.dashscope_api_key or "",
        dashscope_http_api_url=settings.dashscope_http_api_url,
        embedding_model=settings.qwen_embedding_model,
        embedding_timeout_seconds=settings.embedding_timeout_seconds,
        embedding_max_retries=settings.embedding_max_retries,
        embedding_batch_size=settings.embedding_batch_size,
        collection=settings.knowledge_collection,
        dense_dimensions=settings.knowledge_dense_dimensions,
        index_revision=settings.knowledge_index_revision,
    )


def document_limits(settings: Settings) -> DocumentLimits:
    return DocumentLimits(
        upload_max_bytes=settings.knowledge_upload_max_bytes,
        extracted_max_chars=settings.knowledge_extracted_max_chars,
        pdf_max_pages=settings.knowledge_pdf_max_pages,
    )


def knowledge_tool_definition(service: KnowledgeService) -> ToolDefinition:
    return ToolDefinition(
        name="knowledge.search",
        description=(
            "Search only the governed knowledge sources attached to this Agent version. "
            "Use it for organization-specific facts, documents, policies, and runbooks."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 500},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=service.execute,
        mutating=False,
        timeout_seconds=20.0,
        max_calls_per_run=3,
    )
