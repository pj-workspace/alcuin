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
    database_path: str = ".data/alcuin.db"
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
    searxng_url: str | None = None
    searxng_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    web_search_total_timeout_seconds: float = Field(default=15.0, ge=3.0, le=60.0)
    web_search_cache_ttl_seconds: float = Field(default=300.0, ge=0.0, le=3600.0)
    web_page_timeout_seconds: float = Field(default=5.0, ge=1.0, le=20.0)
    web_page_cache_ttl_seconds: float = Field(default=1800.0, ge=0.0, le=86_400.0)
    web_page_max_bytes: int = Field(default=524_288, ge=16_384, le=2_097_152)
    web_page_max_chars: int = Field(default=6_000, ge=1_000, le=50_000)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

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
