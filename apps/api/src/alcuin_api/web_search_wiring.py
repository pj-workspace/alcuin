from __future__ import annotations

from alcuin_web_search import WebSearchConfig, WebSearchService

from .config import Settings
from .tools import ToolDefinition


def web_search_config(settings: Settings) -> WebSearchConfig:
    return WebSearchConfig(
        searxng_url=settings.searxng_url or "",
        provider_timeout_seconds=settings.searxng_timeout_seconds,
        total_timeout_seconds=settings.web_search_total_timeout_seconds,
        search_cache_ttl_seconds=settings.web_search_cache_ttl_seconds,
        stale_if_error_seconds=settings.web_search_stale_if_error_seconds,
        page_timeout_seconds=settings.web_page_timeout_seconds,
        page_cache_ttl_seconds=settings.web_page_cache_ttl_seconds,
        page_max_bytes=settings.web_page_max_bytes,
        page_max_chars=settings.web_page_max_chars,
    )


def web_search_tool_definition(service: WebSearchService) -> ToolDefinition:
    return ToolDefinition(
        name="web.search",
        description=(
            "Search the public web for current or public information. Use quick depth for "
            "normal fact finding and deep depth only when result snippets are insufficient."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "depth": {
                    "type": "string",
                    "enum": ["quick", "deep"],
                    "default": "quick",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
                "language": {
                    "type": "string",
                    "enum": ["auto", "zh", "en"],
                    "default": "auto",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=service.execute,
        mutating=False,
        timeout_seconds=max(3.0, service.config.total_timeout_seconds),
        max_calls_per_run=2,
    )
