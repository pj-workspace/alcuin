from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentLimits:
    upload_max_bytes: int = 8 * 1024 * 1024
    extracted_max_chars: int = 2_000_000
    pdf_max_pages: int = 500

    def __post_init__(self) -> None:
        if self.upload_max_bytes < 1:
            raise ValueError("upload_max_bytes must be positive")
        if self.extracted_max_chars < 1:
            raise ValueError("extracted_max_chars must be positive")
        if self.pdf_max_pages < 1:
            raise ValueError("pdf_max_pages must be positive")


@dataclass(frozen=True)
class KnowledgeConfig:
    qdrant_url: str
    dashscope_api_key: str
    qdrant_api_key: str | None = None
    qdrant_timeout_seconds: int = 10
    dashscope_http_api_url: str = "https://dashscope.aliyuncs.com/api/v1"
    embedding_model: str = "text-embedding-v3"
    embedding_timeout_seconds: float = 20.0
    embedding_max_retries: int = 2
    embedding_batch_size: int = 10
    collection: str = "alcuin_knowledge_qwen_native_v1"
    dense_dimensions: int = 1_024
    index_revision: str = "qwen-native-v1"

    def __post_init__(self) -> None:
        if not self.qdrant_url.strip():
            raise ValueError("qdrant_url is required")
        if not self.dashscope_api_key.strip():
            raise ValueError("dashscope_api_key is required")
        if not 1 <= self.embedding_batch_size <= 10:
            raise ValueError("embedding_batch_size must be between 1 and 10")
        if self.dense_dimensions < 1:
            raise ValueError("dense_dimensions must be positive")
        if self.qdrant_timeout_seconds < 1 or self.embedding_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
