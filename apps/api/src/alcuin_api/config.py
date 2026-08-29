from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    api_key: str | None
    base_url: str
    default_model: str
    protocol: str
    input_modalities: tuple[str, ...]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALCUIN_",
        env_file=(".env", "../../.env"),
        extra="ignore",
    )

    app_name: str = "Alcuin API"
    environment: str = "development"
    database_url: str = "postgresql://alcuin:alcuin@localhost:5432/alcuin"
    postgres_pool_min_size: int = Field(default=1, ge=1, le=20)
    postgres_pool_max_size: int = Field(default=10, ge=1, le=100)
    postgres_pool_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    signing_secret: str = "alcuin-development-signing-secret"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    provider_mode: str = Field(default="responses", pattern="^(responses|chat_completions)$")
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash-vision-exp"
    deepseek_protocol: str = Field(default="chat_completions", pattern="^(responses|chat_completions)$")
    context_window_tokens: int = Field(default=131_072, ge=8_192, le=2_000_000)
    context_reserved_output_tokens: int = Field(default=4_096, ge=256, le=131_072)
    context_reserved_tool_tokens: int = Field(default=8_192, ge=0, le=131_072)
    context_compaction_trigger_ratio: float = Field(default=0.8, gt=0.0, le=1.0)
    searxng_url: str | None = None
    searxng_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    web_search_total_timeout_seconds: float = Field(default=15.0, ge=3.0, le=60.0)
    web_search_cache_ttl_seconds: float = Field(default=300.0, ge=0.0, le=3600.0)
    web_search_stale_if_error_seconds: float = Field(
        default=1_800.0,
        ge=0.0,
        le=86_400.0,
    )
    web_page_timeout_seconds: float = Field(default=5.0, ge=1.0, le=20.0)
    web_page_cache_ttl_seconds: float = Field(default=1800.0, ge=0.0, le=86_400.0)
    web_page_max_bytes: int = Field(default=524_288, ge=16_384, le=2_097_152)
    web_page_max_chars: int = Field(default=6_000, ge=1_000, le=50_000)
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_timeout_seconds: int = Field(default=10, ge=1, le=60)
    dashscope_api_key: str | None = None
    dashscope_http_api_url: str = (
        "https://dashscope.aliyuncs.com/api/v1"
    )
    qwen_embedding_model: str = "text-embedding-v3"
    embedding_timeout_seconds: float = Field(default=20.0, ge=2.0, le=60.0)
    embedding_max_retries: int = Field(default=2, ge=0, le=4)
    embedding_batch_size: int = Field(default=10, ge=1, le=10)
    knowledge_collection: str = Field(
        default="alcuin_knowledge_qwen_native_v1",
        pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{2,127}$",
    )
    knowledge_dense_dimensions: int = Field(default=1_024, ge=32, le=4_096)
    knowledge_index_revision: str = "qwen-native-v1"
    knowledge_upload_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=64 * 1024,
        le=32 * 1024 * 1024,
    )
    knowledge_extracted_max_chars: int = Field(
        default=2_000_000,
        ge=1_000,
        le=5_000_000,
    )
    knowledge_pdf_max_pages: int = Field(default=500, ge=1, le=2_000)
    extension_allow_private_networks: bool = False
    extension_health_timeout_seconds: float = Field(default=5.0, ge=1.0, le=20.0)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def model_post_init(self, context: object) -> None:
        del context
        if self.postgres_pool_max_size < self.postgres_pool_min_size:
            raise ValueError(
                "postgres_pool_max_size must be greater than or equal to postgres_pool_min_size"
            )
        if (
            self.context_reserved_output_tokens + self.context_reserved_tool_tokens
            >= self.context_window_tokens
        ):
            raise ValueError(
                "context token reserves must leave capacity for model input"
            )

    def provider(self, provider_id: str) -> ProviderConfig:
        if provider_id == "deepseek":
            return ProviderConfig(
                id="deepseek",
                api_key=self.deepseek_api_key,
                base_url=self.deepseek_base_url,
                default_model=self.deepseek_model,
                protocol=self.deepseek_protocol,
                input_modalities=("text", "image")
                if self.deepseek_model == "deepseek-v4-flash-vision-exp"
                else ("text",),
            )
        return ProviderConfig(
            id="openai-compatible",
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            default_model=self.openai_model,
            protocol=self.provider_mode,
            input_modalities=("text", "image"),
        )

    def provider_statuses(self) -> list[dict[str, object]]:
        return [
            {
                "id": provider.id,
                "configured": bool(provider.api_key),
                "base_url": provider.base_url,
                "default_model": provider.default_model,
                "protocol": provider.protocol,
                "input_modalities": list(provider.input_modalities),
            }
            for provider in (self.provider("deepseek"), self.provider("openai-compatible"))
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
