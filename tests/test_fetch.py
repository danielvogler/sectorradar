"""The crawler's politeness rules and its resumability."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from sectorradar import db, fetch
from sectorradar.config import ConfigError, Segment, Settings

SEGMENT = Segment.model_validate(
    {
        "slug": "test-seg",
        "name": "Test market, Somewhere",
        "geo": {"country": "CH"},
        "inclusion": "Include companies that sell widgets as a named service.",
        "tiers": {1: "primary"},
    }
)

PAGE = "<html><body><nav>menu</nav><p>We build LLM agents for clients.</p></body></html>"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(contact="test@example.ch", db_path=tmp_path / "radar.db")


def test_main_text_drops_navigation_and_scripts() -> None:
    html = "<html><body><nav>skip</nav><script>x=1</script><p>Keep this.</p></body></html>"
    text = fetch.main_text(html)
    assert "Keep this." in text
    assert "skip" not in text
    assert "x=1" not in text


def test_main_text_keeps_the_footer_because_that_is_where_the_address_is() -> None:
    """Stripping <footer> looked tidy and silently destroyed the location data.

    Measured on real fetched pages: every page that contained a recognisable
    Swiss postal address kept it only in the footer, so removing the element
    lost 100% of them, and 61 of 160 enriched companies had no city as a
    result. Which canton a company is in — and whether it is in the country at
    all — is decided by this text.
    """
    html = (
        "<html><body><p>We build agents.</p>"
        "<footer>Ergon Informatik AG, Merkurstrasse 43, 8032 Zürich</footer>"
        "</body></html>"
    )
    text = fetch.main_text(html)
    assert "8032 Zürich" in text
    assert "Merkurstrasse" in text


def test_content_sha_ignores_whitespace_churn() -> None:
    """Reflowed markup is not a changed page, and must not trigger re-extraction."""
    assert fetch.content_sha("a  b\n c") == fetch.content_sha("a b c")


def test_content_sha_notices_a_real_change() -> None:
    assert fetch.content_sha("a b") != fetch.content_sha("a c")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, "ok"),
        (429, "ok"),
        (401, "ok"),
        (200, "Please complete the CAPTCHA"),
        (200, "Access denied"),
        (200, "Checking your browser — Cloudflare"),
    ],
)
def test_block_pages_are_recognised(status: int, body: str) -> None:
    assert fetch.looks_blocked(status, body)


def test_a_normal_page_is_not_a_block() -> None:
    assert not fetch.looks_blocked(200, PAGE)


def test_internal_links_stay_on_host() -> None:
    html = (
        '<a href="/a">a</a><a href="https://other.ch/b">b</a>'
        '<a href="mailto:x@y.ch">m</a><a href="#top">t</a>'
    )
    links = fetch.internal_links(html, "https://example.ch/")
    assert links == ["https://example.ch/a"]


def test_crawling_refuses_without_a_contact_address(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Never send an anonymous User-Agent to somebody's server."""
    anonymous = Settings(contact=None, db_path=tmp_path / "radar.db")
    with pytest.raises(ConfigError, match="SECTORRADAR_CONTACT"):
        fetch.fetch(conn, SEGMENT, anonymous)


def test_user_agent_carries_the_contact(settings: Settings) -> None:
    assert "test@example.ch" in settings.user_agent()


def _company(conn: sqlite3.Connection, domain: str = "example.ch") -> int:
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(conn, domain=domain, canonical_name=domain)
    db.upsert_membership(conn, segment_slug=SEGMENT.slug, company_id=company_id)
    conn.commit()
    return company_id


def _transport(handler: object) -> object:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


def test_fetch_stores_pages_and_commits_as_it_goes(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-hour crawl must not lose everything to one interrupt."""
    _company(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path in ("/", "/about"):
            return httpx.Response(200, text=PAGE)
        return httpx.Response(404)

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    report = fetch.fetch(conn, SEGMENT, settings)

    assert report.stored >= 1
    assert conn.execute("SELECT COUNT(*) AS n FROM page").fetchone()["n"] >= 1


def test_robots_disallow_is_obeyed(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _company(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(200, text=PAGE)  # pragma: no cover

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    report = fetch.fetch(conn, SEGMENT, settings)

    assert report.stored == 0
    assert report.disallowed > 0


def test_a_bot_block_stops_that_host(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back off and record. Never work around a block."""
    _company(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="Access denied")

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    report = fetch.fetch(conn, SEGMENT, settings)

    assert report.blocked >= 1
    assert "example.ch" in report.blocked_hosts
    assert report.requested == 1, "should stop probing a host that blocked us"


def test_concurrent_crawl_still_writes_every_company(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallelising across hosts must not lose or duplicate a company's pages."""
    for i in range(12):
        _company(conn, f"firm{i}.ch")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(404)

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    report = fetch.fetch(conn, SEGMENT, settings, workers=6)

    assert report.companies == 12
    assert report.stored == 12
    rows = conn.execute("SELECT COUNT(DISTINCT company_id) AS n FROM page").fetchone()
    assert rows["n"] == 12


def test_a_second_run_skips_everything_already_stored(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """content_sha skipping is what makes re-running cheap enough to do often."""
    _company(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(404)

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    fetch.fetch(conn, SEGMENT, settings)
    second = fetch.fetch(conn, SEGMENT, settings)

    assert second.stored == 0
    assert second.unchanged >= 1


# --- following links to reference work --------------------------------------


@pytest.mark.parametrize(
    ("href", "text"),
    [
        ("/referenzen", "Referenzen"),
        ("/case-studies", "Case Studies"),
        ("/projekte", "Unsere Projekte"),
        ("/kunden", "Kunden"),
        ("/en/clients", "Clients"),
        ("/success-stories", "Success Stories"),
        ("/portfolio", "Portfolio"),
        # German sites label the page one way and route it another, so href
        # and anchor text both have to be read.
        ("/arbeiten", "Was wir gemacht haben"),
        ("/p/17", "Referenzprojekte"),
    ],
)
def test_reference_links_are_recognised(href: str, text: str) -> None:
    assert fetch.looks_like_reference_link(href, text)


@pytest.mark.parametrize(
    ("href", "text"),
    [
        ("/blog/why-ai-matters", "Why AI matters"),
        ("/news/2026", "News"),
        ("/karriere", "Karriere"),
        ("/datenschutz", "Datenschutz"),
        ("/whitepaper.pdf", "Download"),
        ("mailto:info@example.ch", "Contact"),
        ("/", "Home"),
    ],
)
def test_irrelevant_links_are_not_followed(href: str, text: str) -> None:
    assert not fetch.looks_like_reference_link(href, text)


def test_reference_links_stay_on_the_same_host() -> None:
    html = (
        '<a href="/referenzen">Referenzen</a>'
        '<a href="https://other.ch/referenzen">Referenzen</a>'
        '<a href="/projekte">Projekte</a>'
    )
    links = fetch.reference_links(html, "https://example.ch/")
    assert links == ["https://example.ch/referenzen", "https://example.ch/projekte"]


def test_reference_links_are_capped() -> None:
    """A paginated reference archive is otherwise unbounded."""
    html = "".join(f'<a href="/case-study-{i}">Case study {i}</a>' for i in range(50))
    assert len(fetch.reference_links(html, "https://example.ch/", limit=8)) == 8


def test_fragments_do_not_create_duplicate_pages() -> None:
    html = '<a href="/referenzen#top">Referenzen</a><a href="/referenzen#bottom">Kunden</a>'
    assert fetch.reference_links(html, "https://example.ch/") == ["https://example.ch/referenzen"]


def test_the_crawl_follows_a_reference_link(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pages that carry client and project detail are not at fixed paths."""
    _company(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(
                200,
                text="<html><body><p>We build agents.</p>"
                '<a href="/unsere-referenzen">Referenzen</a></body></html>',
            )
        if path == "/unsere-referenzen":
            return httpx.Response(200, text="<html><body><p>Projekt für Acme AG.</p></body></html>")
        return httpx.Response(404)

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    report = fetch.fetch(conn, SEGMENT, settings)

    urls = {r["url"] for r in conn.execute("SELECT url FROM page")}
    assert "https://example.ch/unsere-referenzen" in urls, "should have followed the link"
    assert report.discovered >= 1


def test_links_are_harvested_from_cached_pages_too(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery must not depend on a page being newly fetched.

    It originally ran only after a successful store, so on any re-run where the
    fixed paths were already cached no links were read at all — the feature
    worked exactly once, on a cold database, and silently did nothing
    afterwards. Measured: one reference page found across 288 companies.
    """
    _company(conn)

    home = '<html><body><p>We build agents.</p><a href="/referenzen">Referenzen</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text=home)
        if path == "/referenzen":
            return httpx.Response(200, text="<html><body><p>Projekt für Acme AG.</p></body></html>")
        return httpx.Response(404)

    monkeypatch.setattr(fetch, "MIN_INTERVAL", 0.0)
    original_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = _transport(handler)
        return original_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)

    # First pass caches the homepage and follows the link.
    fetch.fetch(conn, SEGMENT, settings)
    conn.execute("DELETE FROM page WHERE url LIKE '%referenzen%'")
    conn.commit()

    # Second pass: the homepage is cached and skipped, so the link can only be
    # found by reading the stored copy.
    report = fetch.fetch(conn, SEGMENT, settings)

    urls = {r["url"] for r in conn.execute("SELECT url FROM page")}
    assert "https://example.ch/referenzen" in urls
    assert report.unchanged >= 1, "the homepage should have been served from cache"
