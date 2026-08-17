"""Web search discovery, behind one provider-agnostic interface.

Four providers are plausible here — Exa, Brave, Tavily and Google Search
grounding via Vertex — and which one is configured should not be visible to
``discover.py``. Each implements :class:`SearchProvider`; adding another is a
new class and a registry entry.

The default is Vertex grounding, because it reuses the Application Default
Credentials the extraction step already needs and so works with no additional
API key. Exa is the better tool for "find companies like this one" and is worth
configuring if a key is available.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from sectorradar.config import ConfigError, Segment, Settings
from sectorradar.logging import get_logger
from sectorradar.models import Candidate
from sectorradar.sources import Ctx

log = get_logger(__name__)

NAME = "websearch"

#: Results to request per query. Past this, precision falls off a cliff.
RESULTS_PER_QUERY = 20


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchHit]: ...


class VertexGroundingProvider:
    """Google Search via Vertex AI grounding.

    Grounding returns the URLs the model consulted alongside its answer, which
    is exactly a search result set. Using it here means one credential covers
    both search and extraction.
    """

    name = "vertex_grounding"

    def __init__(self, project: str, location: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model

    def search(self, query: str, limit: int) -> list[SearchHit]:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=f"{query}. List each company and the URL of its own website.",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )

        hits: list[SearchHit] = []
        for candidate in response.candidates or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web is None:
                    continue
                hit = self._to_hit(getattr(web, "title", None), getattr(web, "uri", None))
                if hit is not None:
                    hits.append(hit)
                if len(hits) >= limit:
                    return hits
        return hits

    @staticmethod
    def _to_hit(title: str | None, uri: str | None) -> SearchHit | None:
        """Recover the real site from a grounding chunk.

        The ``uri`` a grounding chunk carries is a
        ``vertexaisearch.cloud.google.com/grounding-api-redirect/...`` link, not
        the page itself — following every one of them would be a request per
        result for information already present. The ``title`` field holds the
        source's bare domain ("example.ch"), which is exactly what resolution
        wants, so prefer it and keep the redirect only as a fallback.
        """
        if title and "." in title and " " not in title.strip():
            return SearchHit(url=f"https://{title.strip().lower()}", title=title.strip())
        if uri:
            return SearchHit(url=uri, title=title)
        return None


class BraveProvider:
    """Brave Search API — cheapest option for breadth."""

    name = "brave"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, limit: int) -> list[SearchHit]:
        response = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(limit, 20)},
            headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
            timeout=20.0,
        )
        response.raise_for_status()
        results = (response.json().get("web") or {}).get("results") or []
        return [SearchHit(url=r["url"], title=r.get("title")) for r in results if r.get("url")]


class ExaProvider:
    """Exa — neural search, the best of these at "find companies like this"."""

    name = "exa"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, limit: int) -> list[SearchHit]:
        response = httpx.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": limit, "type": "auto"},
            headers={"x-api-key": self._key, "Content-Type": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return [
            SearchHit(url=r["url"], title=r.get("title"))
            for r in response.json().get("results", [])
            if r.get("url")
        ]


class TavilyProvider:
    """Tavily — agent-shaped API, fine for this use."""

    name = "tavily"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, limit: int) -> list[SearchHit]:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self._key, "query": query, "max_results": limit},
            timeout=30.0,
        )
        response.raise_for_status()
        return [
            SearchHit(url=r["url"], title=r.get("title"))
            for r in response.json().get("results", [])
            if r.get("url")
        ]


def get_provider(settings: Settings) -> SearchProvider:
    """Build the configured provider, or say precisely what is missing."""
    choice = settings.search_provider.lower()

    if choice == "vertex_grounding":
        if not settings.gcp_project:
            msg = (
                "GOOGLE_CLOUD_PROJECT is unset. The vertex_grounding search provider "
                "needs a project and Application Default Credentials."
            )
            raise ConfigError(msg)
        return VertexGroundingProvider(
            settings.gcp_project, settings.gcp_location, settings.llm_model
        )

    keyed: dict[str, tuple[str | None, type]] = {
        "exa": (settings.exa_api_key, ExaProvider),
        "brave": (settings.brave_api_key, BraveProvider),
        "tavily": (settings.tavily_api_key, TavilyProvider),
    }
    if choice in keyed:
        key, provider_class = keyed[choice]
        if not key:
            msg = f"SECTORRADAR_SEARCH_PROVIDER is '{choice}' but its API key is unset in .env"
            raise ConfigError(msg)
        provider: SearchProvider = provider_class(key)
        return provider

    msg = (
        f"unknown SECTORRADAR_SEARCH_PROVIDER '{settings.search_provider}'. "
        "Supported: vertex_grounding, exa, brave, tavily."
    )
    raise ConfigError(msg)


def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
    """Run each of the segment's queries and yield what comes back."""
    config = segment.source(NAME)
    queries: list[str] = list(getattr(config, "queries", None) or [])
    if not queries:
        log.warning("websearch.no_queries", segment=segment.slug)
        return

    provider = get_provider(ctx.settings)
    emitted = 0

    for query in queries:
        if ctx.limit is not None and emitted >= ctx.limit:
            return
        try:
            hits = provider.search(query, RESULTS_PER_QUERY)
        except (httpx.HTTPError, ConfigError) as exc:
            # One failed query is not a failed run. Record and carry on.
            log.warning("websearch.query_failed", query=query, error=str(exc))
            continue

        log.info("websearch.query", query=query, hits=len(hits), provider=provider.name)
        for hit in hits:
            if ctx.limit is not None and emitted >= ctx.limit:
                return
            emitted += 1
            yield Candidate(
                segment_slug=segment.slug,
                source=NAME,
                raw_url=hit.url,
                raw_name=hit.title,
                source_detail=f"{provider.name}: {query}",
            )
