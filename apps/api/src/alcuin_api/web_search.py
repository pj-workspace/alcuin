from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit

import httpx

from .config import Settings
from .tools import ToolCitation, ToolContext, ToolDefinition, ToolError, ToolResult


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str
    source: str
    content: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
        }
        if self.content:
            payload["content"] = self.content
        return payload


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "article", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1
        elif tag in {"p", "li", "article", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)

    def text(self, max_chars: int) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        compact = "\n".join(line for line in lines if line)
        return compact[:max_chars]


class _TtlCache:
    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        row = self._values.get(key)
        if row is None:
            return None
        created_at, value = row
        if time.monotonic() - created_at > self.ttl_seconds:
            self._values.pop(key, None)
            return None
        self._values.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._values[key] = (time.monotonic(), value)
        self._values.move_to_end(key)
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)


PublicUrlValidator = Callable[[str], Awaitable[bool]]


class WebSearchService:
    """SearXNG search with bounded deep reads, structured citations, and warm caches."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        public_url_validator: PublicUrlValidator | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.searxng_timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "Alcuin/0.1 web.search"},
        )
        self.public_url_validator = public_url_validator or is_public_http_url
        self.search_cache = _TtlCache(
            ttl_seconds=settings.web_search_cache_ttl_seconds,
            max_entries=256,
        )
        self.page_cache = _TtlCache(
            ttl_seconds=settings.web_page_cache_ttl_seconds,
            max_entries=512,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def tool_definition(self) -> ToolDefinition:
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
                    "depth": {"type": "string", "enum": ["quick", "deep"], "default": "quick"},
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
            handler=self.execute,
            mutating=False,
            timeout_seconds=max(3.0, self.settings.web_search_total_timeout_seconds),
            max_calls_per_run=2,
        )

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        del context
        query = str(arguments["query"]).strip()
        depth = str(arguments.get("depth") or "quick")
        max_results = int(arguments.get("max_results") or 5)
        language = str(arguments.get("language") or "auto")
        hits = await self.search(
            query,
            depth=depth,
            max_results=max_results,
            language=language,
        )
        citations = tuple(
            ToolCitation(
                label=hit.title,
                source=hit.source or "SearXNG",
                locator=hit.url,
                snippet=hit.snippet,
                metadata={"kind": "web"},
            )
            for hit in hits
        )
        return ToolResult(
            data={
                "query": query,
                "depth": depth,
                "hits": [hit.as_dict() for hit in hits],
            },
            summary=f"Found {len(hits)} web result{'s' if len(hits) != 1 else ''}",
            citations=citations,
        )

    async def search(
        self,
        query: str,
        *,
        depth: str,
        max_results: int,
        language: str,
    ) -> list[WebSearchHit]:
        base = (self.settings.searxng_url or "").strip().rstrip("/")
        if not base:
            raise ToolError("web_search_unconfigured", "Public web search is not configured")

        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ToolError("invalid_arguments", "Search query cannot be empty")
        if depth not in {"quick", "deep"}:
            raise ToolError("invalid_arguments", "Search depth must be quick or deep")
        if language not in {"auto", "zh", "en"}:
            raise ToolError("invalid_arguments", "Search language must be auto, zh, or en")
        max_results = max(1, min(int(max_results), 10))
        cache_key = f"{normalized_query.casefold()}|{language}|{max_results}|{depth}"
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        params = {
            "q": normalized_query,
            "format": "json",
            "language": "all" if language == "auto" else language,
        }
        try:
            response = await self.client.get(f"{base}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError("web_search_unavailable", "Public web search is temporarily unavailable") from exc

        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ToolError("invalid_search_response", "Public web search returned an invalid response")

        hits: list[WebSearchHit] = []
        seen_urls: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = canonical_url(str(row.get("url") or ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            hits.append(
                WebSearchHit(
                    title=_clean_text(row.get("title")) or "Untitled result",
                    url=url,
                    snippet=_clean_text(row.get("content")),
                    source=_clean_text(row.get("engine")) or "SearXNG",
                )
            )
            if len(hits) >= max_results:
                break

        if depth == "deep" and hits:
            reads = await asyncio.gather(
                *(self._read_page(hit.url) for hit in hits[:3]),
                return_exceptions=True,
            )
            enriched: list[WebSearchHit] = []
            for index, hit in enumerate(hits):
                content = reads[index] if index < len(reads) and isinstance(reads[index], str) else None
                enriched.append(
                    WebSearchHit(
                        title=hit.title,
                        url=hit.url,
                        snippet=hit.snippet,
                        source=hit.source,
                        content=content,
                    )
                )
            hits = enriched

        self.search_cache.set(cache_key, tuple(hits))
        return hits

    async def _read_page(self, url: str) -> str | None:
        cached = self.page_cache.get(url)
        if cached is not None:
            return str(cached)
        if not await self.public_url_validator(url):
            return None

        try:
            async with asyncio.timeout(self.settings.web_page_timeout_seconds):
                async with self.client.stream("GET", url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not any(
                        allowed in content_type
                        for allowed in ("text/html", "application/xhtml+xml", "text/plain")
                    ):
                        return None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= self.settings.web_page_max_bytes:
                            break
        except (TimeoutError, httpx.HTTPError):
            return None

        text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        if "text/plain" in content_type:
            extracted = "\n".join(
                line for line in (" ".join(row.split()) for row in text.splitlines()) if line
            )[: self.settings.web_page_max_chars]
        else:
            parser = _TextExtractor()
            parser.feed(text)
            extracted = parser.text(self.settings.web_page_max_chars)
        if extracted:
            self.page_cache.set(url, extracted)
        return extracted or None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def canonical_url(value: str) -> str:
    try:
        clean, _fragment = urldefrag(value.strip())
        parsed = urlsplit(clean)
        port_value = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    port = f":{port_value}" if port_value else ""
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, parsed.query, ""))


async def is_public_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            return False
    return bool(infos)
