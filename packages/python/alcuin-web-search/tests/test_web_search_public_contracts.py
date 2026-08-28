"""Public contract tests with a package-unique module name for monorepo collection."""

from __future__ import annotations

import pytest

from alcuin_web_search import WebSearchConfig, WebSearchQuery, canonical_url


def test_query_contract_and_configuration_are_provider_neutral() -> None:
    query = WebSearchQuery(
        query="critical minerals outlook",
        depth="deep",
        max_results=6,
        language="en",
    )
    config = WebSearchConfig(searxng_url="http://search.internal")

    assert query.max_results == 6
    assert config.total_timeout_seconds == 15.0
    assert config.stale_if_error_seconds == 1_800.0

    with pytest.raises(ValueError, match="HTTP"):
        WebSearchConfig(searxng_url="")


def test_canonical_url_removes_tracking_without_losing_semantic_parameters() -> None:
    assert canonical_url(
        "https://Example.COM:443/report/?metal=tungsten&utm_source=mail&year=2026#risk"
    ) == "https://example.com/report?metal=tungsten&year=2026"
