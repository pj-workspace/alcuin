from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from alcuin_web_search import WebSearchConfig, WebSearchService, canonical_url
from alcuin_api.tools import ToolContext, ToolError, ToolExecutor, ToolRegistry
from alcuin_api.web_search_wiring import web_search_tool_definition


def _settings(**overrides) -> WebSearchConfig:
    return WebSearchConfig(
        searxng_url="http://searx.test",
        **overrides,
    )


@pytest.mark.asyncio
async def test_quick_search_deduplicates_results_and_uses_ttl_cache() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/search"
        assert request.url.params["q"] == "Alcuin agent"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "  Alcuin   Agent ",
                        "url": "https://example.com/docs/#intro",
                        "content": "Composable agent platform",
                        "engine": "brave",
                    },
                    {
                        "title": "Duplicate",
                        "url": "https://example.com/docs",
                        "content": "duplicate",
                        "engine": "bing",
                    },
                    {
                        "title": "Unsafe",
                        "url": "http://127.0.0.1/admin",
                        "content": "must be discarded",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WebSearchService(_settings(), client=client)
        first = await service.search(
            "  Alcuin   agent ", depth="quick", max_results=5, language="auto"
        )
        second = await service.search(
            "Alcuin agent", depth="quick", max_results=5, language="auto"
        )

    assert calls == 1
    assert first == second
    assert len(first.hits) == 1
    assert first.hits[0].title == "Alcuin Agent"
    assert first.hits[0].url == "https://example.com/docs"
    assert first.hits[0].content is None
    assert first.degraded is False


@pytest.mark.asyncio
async def test_deep_search_reads_only_bounded_public_pages_and_extracts_text() -> None:
    page_calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Architecture",
                            "url": "https://docs.example.com/alcuin",
                            "content": "Architecture notes",
                            "engine": "brave",
                        }
                    ]
                },
            )
        page_calls.append(str(request.url))
        return httpx.Response(
            200,
            text=(
                "<html><style>hidden</style><body><h1>Alcuin</h1>"
                "<script>ignore me</script><p>Agent Studio and extensions.</p></body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    async def allow_public(_url: str) -> bool:
        return True

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WebSearchService(
            _settings(),
            client=client,
            public_url_validator=allow_public,
        )
        hits = await service.search(
            "architecture", depth="deep", max_results=5, language="en"
        )

    assert page_calls == ["https://docs.example.com/alcuin"]
    assert hits.hits[0].content == "Alcuin\nAgent Studio and extensions."
    assert "hidden" not in (hits.hits[0].content or "")
    assert "ignore me" not in (hits.hits[0].content or "")
    assert hits.degraded is False


@pytest.mark.asyncio
async def test_web_search_tool_returns_structured_hits_and_citations() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Alcuin",
                        "url": "https://example.com/alcuin",
                        "content": "Agent platform",
                        "engine": "brave",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WebSearchService(_settings(), client=client)
        executor = ToolExecutor(
            ToolRegistry([web_search_tool_definition(service)])
        )
        execution = await executor.execute(
            "web.search",
            {"query": "Alcuin", "depth": "quick", "max_results": 5},
            allowed_names=["web.search"],
            context=ToolContext("ws_alpha", "run_1", {}),
        )

    assert execution.result.data == {
        "query": "Alcuin",
        "depth": "quick",
        "language": "auto",
        "degraded": False,
        "warnings": [],
        "hits": [
            {
                "title": "Alcuin",
                "url": "https://example.com/alcuin",
                "snippet": "Agent platform",
                "source": "brave",
            }
        ],
    }
    assert execution.result.citations[0].locator == "https://example.com/alcuin"
    assert execution.result.citations[0].metadata == {
        "kind": "web",
        "degraded": False,
    }


@pytest.mark.asyncio
async def test_web_search_normalizes_provider_failure_and_unconfigured_state() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=json.dumps({"error": "internal details"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WebSearchService(_settings(), client=client)
        with pytest.raises(ToolError) as invalid:
            await service.search("   ", depth="quick", max_results=5, language="auto")
        with pytest.raises(ToolError) as unavailable:
            await service.search("test", depth="quick", max_results=5, language="auto")

    assert invalid.value.code == "invalid_arguments"
    assert unavailable.value.code == "web_search_unavailable"
    assert "internal details" not in unavailable.value.message
    with pytest.raises(ValueError, match="HTTP"):
        WebSearchConfig(searxng_url="")


@pytest.mark.asyncio
async def test_search_returns_explicit_stale_cache_degradation() -> None:
    fail = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503, text="provider unavailable")
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Cached evidence",
                        "url": "https://example.com/evidence?utm_source=test",
                        "content": "Previously retrieved evidence",
                        "engine": "brave",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WebSearchService(
            _settings(search_cache_ttl_seconds=0, stale_if_error_seconds=60),
            client=client,
        )
        fresh = await service.search(
            "cached evidence", depth="quick", max_results=5, language="en"
        )
        fail = True
        stale = await service.search(
            "cached evidence", depth="quick", max_results=5, language="en"
        )

    assert fresh.degraded is False
    assert stale.degraded is True
    assert stale.warnings == ("stale_cache",)
    assert stale.hits == fresh.hits


@pytest.mark.asyncio
async def test_search_enforces_total_timeout_without_cached_evidence() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WebSearchService(
            _settings(total_timeout_seconds=0.01),
            client=client,
        )
        with pytest.raises(ToolError) as timeout:
            await service.search(
                "deadline", depth="quick", max_results=5, language="auto"
            )

    assert timeout.value.code == "web_search_timeout"


def test_canonical_url_rejects_private_and_non_http_targets() -> None:
    assert canonical_url("http://127.0.0.1/admin") == ""
    assert canonical_url("http://[::1]/admin") == ""
    assert canonical_url("file:///etc/passwd") == ""
    assert canonical_url("https://Example.COM/path/#section") == "https://example.com/path"
    assert canonical_url(
        "https://Example.COM:443/path/?b=2&utm_source=newsletter&a=1&gclid=secret"
    ) == "https://example.com/path?a=1&b=2"
