from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urldefrag, urlsplit, urlunsplit

import httpx
from alcuin_core.tools import ToolCitation, ToolContext, ToolError, ToolResult

from .config import WebSearchConfig


SearchDepth = Literal["quick", "deep"]
SearchLanguage = Literal["auto", "zh", "en"]


@dataclass(frozen=True)
class WebSearchQuery:
    query: str
    depth: SearchDepth = "quick"
    max_results: int = 5
    language: SearchLanguage = "auto"


@dataclass(frozen=True)
class WebSearchResponse:
    query: WebSearchQuery
    hits: tuple["WebSearchHit", ...]
    degraded: bool = False
    warnings: tuple[str, ...] = ()


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
    def __init__(
        self,
        *,
        ttl_seconds: float,
        stale_seconds: float = 0.0,
        max_entries: int,
    ) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.stale_seconds = max(0.0, stale_seconds)
        self.max_entries = max(1, max_entries)
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str, *, allow_stale: bool = False) -> Any | None:
        row = self._values.get(key)
        if row is None:
            return None
        created_at, value = row
        age = time.monotonic() - created_at
        if age > self.ttl_seconds + self.stale_seconds:
            self._values.pop(key, None)
            return None
        if age > self.ttl_seconds and not allow_stale:
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
        config: WebSearchConfig,
        *,
        client: httpx.AsyncClient | None = None,
        public_url_validator: PublicUrlValidator | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.provider_timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "Alcuin/0.1 web.search"},
        )
        self.public_url_validator = public_url_validator or is_public_http_url
        self.search_cache = _TtlCache(
            ttl_seconds=config.search_cache_ttl_seconds,
            stale_seconds=config.stale_if_error_seconds,
            max_entries=256,
        )
        self.page_cache = _TtlCache(
            ttl_seconds=config.page_cache_ttl_seconds,
            max_entries=512,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        del context
        response = await self.search(
            str(arguments["query"]),
            depth=str(arguments.get("depth") or "quick"),
            max_results=int(arguments.get("max_results") or 5),
            language=str(arguments.get("language") or "auto"),
        )
        citations = tuple(
            ToolCitation(
                label=hit.title,
                source=hit.source or "SearXNG",
                locator=hit.url,
                snippet=hit.snippet,
                metadata={"kind": "web", "degraded": response.degraded},
            )
            for hit in response.hits
        )
        return ToolResult(
            data={
                "query": response.query.query,
                "depth": response.query.depth,
                "language": response.query.language,
                "degraded": response.degraded,
                "warnings": list(response.warnings),
                "hits": [hit.as_dict() for hit in response.hits],
            },
            summary=(
                f"Found {len(response.hits)} web result"
                f"{'s' if len(response.hits) != 1 else ''}"
                f"{' with degraded retrieval' if response.degraded else ''}"
            ),
            citations=citations,
        )

    async def search(
        self,
        query: str,
        *,
        depth: str,
        max_results: int,
        language: str,
    ) -> WebSearchResponse:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ToolError("invalid_arguments", "Search query cannot be empty")
        if depth not in {"quick", "deep"}:
            raise ToolError("invalid_arguments", "Search depth must be quick or deep")
        if language not in {"auto", "zh", "en"}:
            raise ToolError("invalid_arguments", "Search language must be auto, zh, or en")
        request = WebSearchQuery(
            query=normalized_query,
            depth=cast(SearchDepth, depth),
            max_results=max(1, min(int(max_results), 10)),
            language=cast(SearchLanguage, language),
        )
        cache_key = (
            f"{request.query.casefold()}|{request.language}|"
            f"{request.max_results}|{request.depth}"
        )
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return cast(WebSearchResponse, cached)

        try:
            async with asyncio.timeout(self.config.total_timeout_seconds):
                result = await self._search_uncached(request)
        except TimeoutError as exc:
            stale = self._stale_response(cache_key, request, "stale_cache")
            if stale:
                return stale
            raise ToolError(
                "web_search_timeout",
                "Public web search exceeded its total deadline",
            ) from exc
        except ToolError as exc:
            if exc.code not in {"web_search_unavailable", "invalid_search_response"}:
                raise
            stale = self._stale_response(cache_key, request, "stale_cache")
            if stale:
                return stale
            raise

        self.search_cache.set(cache_key, result)
        return result

    async def _search_uncached(
        self,
        request: WebSearchQuery,
    ) -> WebSearchResponse:
        base = self.config.searxng_url.strip().rstrip("/")

        params = {
            "q": request.query,
            "format": "json",
            "language": "all" if request.language == "auto" else request.language,
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
            if len(hits) >= request.max_results:
                break

        warnings: list[str] = []
        if request.depth == "deep" and hits:
            reads = await asyncio.gather(
                *(self._read_page(hit.url) for hit in hits[:3]),
                return_exceptions=True,
            )
            if any(not isinstance(read, str) or not read for read in reads):
                warnings.append("deep_read_partial")
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

        return WebSearchResponse(
            query=request,
            hits=tuple(hits),
            degraded=bool(warnings),
            warnings=tuple(warnings),
        )

    def _stale_response(
        self,
        cache_key: str,
        request: WebSearchQuery,
        warning: str,
    ) -> WebSearchResponse | None:
        cached = self.search_cache.get(cache_key, allow_stale=True)
        if cached is None:
            return None
        previous = cast(WebSearchResponse, cached)
        warnings = tuple(dict.fromkeys((*previous.warnings, warning)))
        return WebSearchResponse(
            query=request,
            hits=previous.hits,
            degraded=True,
            warnings=warnings,
        )

    async def _read_page(self, url: str) -> str | None:
        cached = self.page_cache.get(url)
        if cached is not None:
            return str(cached)
        if not await self.public_url_validator(url):
            return None

        try:
            async with asyncio.timeout(self.config.page_timeout_seconds):
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
                        if len(body) >= self.config.page_max_bytes:
                            break
        except (TimeoutError, httpx.HTTPError):
            return None

        text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        if "text/plain" in content_type:
            extracted = "\n".join(
                line for line in (" ".join(row.split()) for row in text.splitlines()) if line
            )[: self.config.page_max_chars]
        else:
            parser = _TextExtractor()
            parser.feed(text)
            extracted = parser.text(self.config.page_max_chars)
        if extracted:
            self.page_cache.set(url, extracted)
        return extracted or None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


_TRACKING_QUERY_KEYS = frozenset(
    {
        "dclid",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "ref_src",
    }
)


def canonical_url(value: str) -> str:
    try:
        clean, _fragment = urldefrag(value.strip())
        parsed = urlsplit(clean)
        port_value = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    if address is not None and address.version == 6:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    port = f":{port_value}" if port_value and port_value != default_port else ""
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in _TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, query, ""))


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
