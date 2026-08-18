"""The discovery sources that talk to the outside world.

All network is mocked. What matters here is not that the HTTP works but that
each source behaves correctly when the outside world misbehaves — a directory
bot-blocks, a provider is unconfigured, a registry has no website field.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sectorradar.config import ConfigError, Segment, Settings
from sectorradar.sources import Ctx, available, directories, jobads, lindas, websearch
from sectorradar.sources.websearch import SearchHit


def _segment(**sources: object) -> Segment:
    return Segment.model_validate(
        {
            "slug": "test-seg",
            "name": "Test market, Somewhere",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that build LLM agents for clients.",
            "tiers": {1: "primary"},
            "sources": sources,
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        contact="test@example.ch",
        db_path=tmp_path / "radar.db",
        gcp_project="test-project",
    )


class FakeProvider:
    name = "fake"

    def __init__(self, *batches: list[SearchHit]) -> None:
        self._batches = list(batches)
        self.queries: list[str] = []

    def search(self, query: str, limit: int) -> list[SearchHit]:
        self.queries.append(query)
        return self._batches.pop(0) if self._batches else []


class ExplodingProvider:
    name = "exploding"

    def search(self, query: str, limit: int) -> list[SearchHit]:
        raise httpx.ConnectError("blocked")


def test_every_source_named_in_the_shipped_segments_is_registered() -> None:
    """A YAML enabling a source this build lacks is a configuration trap."""
    from sectorradar.config import load_segment

    for slug in ("pilates-zurich", "ai-assurance-ch"):
        for name in load_segment(slug).enabled_sources():
            assert name in available(), f"{slug}.yaml enables unregistered source '{name}'"


# --- websearch --------------------------------------------------------------


def test_grounding_prefers_the_title_over_the_redirect_uri() -> None:
    """Grounding chunks carry a vertexaisearch redirect; the domain is the title."""
    hit = websearch.VertexGroundingProvider._to_hit(
        "example.ch", "https://vertexaisearch.cloud.google.com/grounding-api-redirect/ABC"
    )
    assert hit is not None
    assert hit.url == "https://example.ch"


def test_grounding_falls_back_to_the_uri_when_the_title_is_prose() -> None:
    hit = websearch.VertexGroundingProvider._to_hit("Some Article Title", "https://real.ch/page")
    assert hit is not None
    assert hit.url == "https://real.ch/page"


def test_grounding_skips_a_chunk_with_neither() -> None:
    assert websearch.VertexGroundingProvider._to_hit(None, None) is None


def test_websearch_emits_a_candidate_per_hit(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider([SearchHit("https://a.ch", "a.ch"), SearchHit("https://b.ch", "b.ch")])
    monkeypatch.setattr(websearch, "get_provider", lambda s: provider)

    segment = _segment(websearch={"enabled": True, "queries": ["ai switzerland"]})
    found = list(websearch.run(segment, Ctx(settings=settings)))

    assert [c.raw_url for c in found] == ["https://a.ch", "https://b.ch"]
    assert all("ai switzerland" in (c.source_detail or "") for c in found)


def test_one_failing_query_does_not_end_the_source(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(websearch, "get_provider", lambda s: ExplodingProvider())
    segment = _segment(websearch={"enabled": True, "queries": ["a", "b"]})
    assert list(websearch.run(segment, Ctx(settings=settings))) == []


def test_websearch_with_no_queries_yields_nothing(settings: Settings) -> None:
    assert list(websearch.run(_segment(websearch={"enabled": True}), Ctx(settings=settings))) == []


def test_an_unknown_search_provider_is_named_in_the_error(settings: Settings) -> None:
    broken = settings.model_copy(update={"search_provider": "altavista"})
    with pytest.raises(ConfigError, match="altavista"):
        websearch.get_provider(broken)


def test_a_keyed_provider_without_its_key_says_which_key(settings: Settings) -> None:
    broken = settings.model_copy(update={"search_provider": "exa", "exa_api_key": None})
    with pytest.raises(ConfigError, match="API key is unset"):
        websearch.get_provider(broken)


def test_vertex_grounding_needs_a_project(settings: Settings) -> None:
    broken = settings.model_copy(update={"gcp_project": None})
    with pytest.raises(ConfigError, match="GOOGLE_CLOUD_PROJECT"):
        websearch.get_provider(broken)


# --- jobads -----------------------------------------------------------------


def test_jobads_searches_boards_rather_than_crawling_them(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job boards carry personal data and bot-block; ask, do not scrape."""
    provider = FakeProvider([SearchHit("https://employer.ch", "employer.ch")])
    monkeypatch.setattr(jobads, "get_provider", lambda s: provider)

    segment = _segment(jobads={"enabled": True, "keywords": ["LangGraph"]})
    found = list(jobads.run(segment, Ctx(settings=settings)))

    assert len(found) == 1
    assert "jobs.ch" in provider.queries[0], "should target boards via the search provider"
    assert found[0].raw_url == "https://employer.ch"


def test_jobads_records_the_keyword_not_the_advert(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing about the posting or the person who placed it is stored."""
    monkeypatch.setattr(
        jobads, "get_provider", lambda s: FakeProvider([SearchHit("https://e.ch", "e.ch")])
    )
    segment = _segment(jobads={"enabled": True, "keywords": ["AI Engineer"]})
    candidate = next(iter(jobads.run(segment, Ctx(settings=settings))))
    assert candidate.source_detail == "hiring signal: AI Engineer"


def test_jobads_without_keywords_yields_nothing(settings: Settings) -> None:
    assert list(jobads.run(_segment(jobads={"enabled": True}), Ctx(settings=settings))) == []


def test_jobads_without_a_search_provider_is_quiet(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unconfigured(_: Settings) -> None:
        raise ConfigError("no provider")

    monkeypatch.setattr(jobads, "get_provider", unconfigured)
    segment = _segment(jobads={"enabled": True, "keywords": ["x"]})
    assert list(jobads.run(segment, Ctx(settings=settings))) == []


# --- directories ------------------------------------------------------------


def test_directories_keeps_company_domains_not_directory_pages(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The company's own site is better evidence than a directory's summary."""
    provider = FakeProvider(
        [
            SearchHit("https://clutch.co/profile/some-agency", "clutch.co"),
            SearchHit("https://realagency.ch", "realagency.ch"),
        ]
    )
    monkeypatch.setattr(directories, "get_provider", lambda s: provider)

    segment = _segment(directories={"enabled": True, "sites": ["clutch.co"], "terms": ["AI"]})
    found = list(directories.run(segment, Ctx(settings=settings)))

    assert [c.raw_url for c in found] == ["https://realagency.ch"]


def test_a_blocking_directory_is_recorded_and_skipped(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expect directories to block. Never build a way around one."""
    monkeypatch.setattr(directories, "get_provider", lambda s: ExplodingProvider())
    segment = _segment(directories={"enabled": True, "sites": ["clutch.co"], "terms": ["AI"]})
    assert list(directories.run(segment, Ctx(settings=settings))) == []


def test_directories_without_sites_yields_nothing(settings: Settings) -> None:
    segment = _segment(directories={"enabled": True})
    assert list(directories.run(segment, Ctx(settings=settings))) == []


# --- lindas -----------------------------------------------------------------


CSV = "company,name,locality\nhttps://register.ld.admin.ch/zefix/company/1,Acme AG,Zürich\n"


def test_lindas_yields_registry_rows_without_a_url(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The register has no website field. Inventing one from the name would be worse."""
    monkeypatch.setattr(
        lindas,
        "search_purpose",
        lambda term, *a, **k: [{"company": "x", "name": "Acme AG", "locality": "Zürich"}],
    )
    segment = _segment(lindas={"enabled": True, "purpose_terms": ["Künstliche Intelligenz"]})
    found = list(lindas.run(segment, Ctx(settings=settings)))

    assert len(found) == 1
    assert found[0].raw_name == "Acme AG"
    assert found[0].raw_url is None, "no domain may be fabricated from a company name"
    assert found[0].raw_city == "Zürich"


def test_lindas_parses_the_csv_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=CSV)

    transport = httpx.MockTransport(handler)
    original_post = httpx.post

    def patched(*args: object, **kwargs: object) -> httpx.Response:
        with httpx.Client(transport=transport) as client:
            return client.post(str(args[0]), **{k: v for k, v in kwargs.items() if k != "timeout"})  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "post", patched)
    rows = lindas.search_purpose("künstliche intelligenz")
    monkeypatch.setattr(httpx, "post", original_post)

    assert rows[0]["name"] == "Acme AG"


def test_lindas_skips_a_row_with_no_name(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lindas, "search_purpose", lambda term, *a, **k: [{"name": "", "locality": "Bern"}]
    )
    segment = _segment(lindas={"enabled": True, "purpose_terms": ["x"]})
    assert list(lindas.run(segment, Ctx(settings=settings))) == []


def test_lindas_without_terms_yields_nothing(settings: Settings) -> None:
    assert list(lindas.run(_segment(lindas={"enabled": True}), Ctx(settings=settings))) == []


def test_a_failing_lindas_query_does_not_end_the_source(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(term: str, *a: object, **k: object) -> list[dict[str, str]]:
        raise httpx.ConnectError("endpoint down")

    monkeypatch.setattr(lindas, "search_purpose", boom)
    segment = _segment(lindas={"enabled": True, "purpose_terms": ["a", "b"]})
    assert list(lindas.run(segment, Ctx(settings=settings))) == []
