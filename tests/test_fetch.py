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
        "name": "Test",
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
