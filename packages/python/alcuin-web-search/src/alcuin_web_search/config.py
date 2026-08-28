from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class WebSearchConfig:
    searxng_url: str
    provider_timeout_seconds: float = 8.0
    total_timeout_seconds: float = 15.0
    search_cache_ttl_seconds: float = 300.0
    stale_if_error_seconds: float = 1_800.0
    page_timeout_seconds: float = 5.0
    page_cache_ttl_seconds: float = 1_800.0
    page_max_bytes: int = 524_288
    page_max_chars: int = 6_000

    def __post_init__(self) -> None:
        endpoint = urlsplit(self.searxng_url.strip())
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
            raise ValueError("searxng_url must be an HTTP(S) endpoint")
        if self.provider_timeout_seconds <= 0 or self.total_timeout_seconds <= 0:
            raise ValueError("search timeouts must be positive")
        if self.page_timeout_seconds <= 0:
            raise ValueError("page_timeout_seconds must be positive")
        if min(
            self.search_cache_ttl_seconds,
            self.stale_if_error_seconds,
            self.page_cache_ttl_seconds,
        ) < 0:
            raise ValueError("cache durations cannot be negative")
        if self.page_max_bytes < 1 or self.page_max_chars < 1:
            raise ValueError("page limits must be positive")
