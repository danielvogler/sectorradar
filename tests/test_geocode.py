"""Geocoding: query construction, the disk cache, and provider fallback.

All network is mocked. The point of these tests is that the module is polite
and frugal — that it asks the smallest number of questions it can, and never
asks the same one twice.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from sectorradar import db, geocode
from sectorradar.config import Segment, Settings
from sectorradar.models import GeoPoint

SEGMENT = Segment.model_validate(
    {
        "slug": "test-seg",
        "name": "Test market, Somewhere",
        "geo": {"country": "CH"},
        "inclusion": "Include companies that sell widgets as a named service.",
        "tiers": {1: "primary"},
    }
)

ZURICH = {"results": [{"attrs": {"lat": 47.3769, "lon": 8.5417, "label": "Zürich"}}]}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(contact="test@example.ch", db_path=tmp_path / "radar.db")


# --- query construction -----------------------------------------------------


@pytest.mark.parametrize(
    ("street", "postal", "city", "canton", "expected"),
    [
        ("Bahnhofstrasse 1", "8001", "Zürich", "ZH", "Bahnhofstrasse 1, 8001, Zürich, Switzerland"),
        (None, None, "Bern", "BE", "Bern, Switzerland"),
        (None, "1200", "Genève", None, "1200, Genève, Switzerland"),
        (None, None, None, "TI", "Ticino, Switzerland"),
    ],
)
def test_build_query_uses_the_most_specific_fields_available(
    street: str | None, postal: str | None, city: str | None, canton: str | None, expected: str
) -> None:
    assert geocode.build_query(street, postal, city, canton) == expected


def test_build_query_gives_up_when_nothing_is_known() -> None:
    """A request that can only return the centroid of Switzerland is not worth making."""
    assert geocode.build_query(None, None, None, None) is None
    assert geocode.build_query("", "", "", "") is None


def test_build_query_ignores_an_unrecognised_canton() -> None:
    assert geocode.build_query(None, None, None, "XX") is None


def test_cache_key_is_whitespace_and_case_insensitive() -> None:
    assert geocode.cache_key("  Zürich,   Switzerland ") == geocode.cache_key("zürich, switzerland")


# --- the cache --------------------------------------------------------------


def test_cache_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "geocode.json"
    cache = geocode.GeocodeCache(path)
    cache.put("zurich", GeoPoint(lat=47.4, lon=8.5, provider="swisstopo"))
    cache.save()

    reloaded = geocode.GeocodeCache(path)
    point = reloaded.get("zurich")
    assert point is not None
    assert point.lat == pytest.approx(47.4)


def test_cache_remembers_a_miss(tmp_path: Path) -> None:
    """A place not found this morning will not be found this afternoon."""
    path = tmp_path / "geocode.json"
    cache = geocode.GeocodeCache(path)
    cache.put("nowhere", None)
    cache.save()

    reloaded = geocode.GeocodeCache(path)
    assert "nowhere" in reloaded
    assert reloaded.get("nowhere") is None


def test_a_corrupt_cache_file_does_not_stop_the_run(tmp_path: Path) -> None:
    path = tmp_path / "geocode.json"
    path.write_text("{not json", encoding="utf-8")
    cache = geocode.GeocodeCache(path)
    assert "anything" not in cache


def test_cache_file_is_human_readable(tmp_path: Path) -> None:
    path = tmp_path / "geocode.json"
    cache = geocode.GeocodeCache(path)
    cache.put("zürich, switzerland", GeoPoint(lat=47.4, lon=8.5, provider="swisstopo"))
    cache.save()
    raw = path.read_text(encoding="utf-8")
    assert "zürich" in raw, "unicode should not be escaped into unreadability"
    assert json.loads(raw)


# --- provider behaviour -----------------------------------------------------


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_swisstopo_is_tried_first(settings: Settings) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.host))
        return httpx.Response(200, json=ZURICH)

    with _client(handler) as client:
        point = geocode.geocode_query(
            client, "Zürich, Switzerland", settings.user_agent(), last_nominatim=[0.0]
        )

    assert point is not None
    assert point.provider == "swisstopo"
    assert calls == ["api3.geo.admin.ch"], "nominatim must not be called when swisstopo answers"


def test_nominatim_is_the_fallback(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "geo.admin.ch" in str(request.url):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200, json=[{"lat": "46.2044", "lon": "6.1432", "display_name": "Genève"}]
        )

    with _client(handler) as client:
        point = geocode.geocode_query(
            client, "Genève, Switzerland", settings.user_agent(), last_nominatim=[0.0]
        )

    assert point is not None
    assert point.provider == "nominatim"
    assert point.lat == pytest.approx(46.2044)


def test_both_providers_failing_yields_none_rather_than_raising(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _client(handler) as client:
        point = geocode.geocode_query(
            client, "Nowhere", settings.user_agent(), last_nominatim=[0.0]
        )
    assert point is None


def test_geocoding_refuses_to_run_without_a_contact_address(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The same rule as the crawler: no anonymous requests to anyone's service."""
    from sectorradar.config import ConfigError

    anonymous = Settings(contact=None, db_path=tmp_path / "radar.db")
    with pytest.raises(ConfigError, match="SECTORRADAR_CONTACT"):
        geocode.geocode(conn, SEGMENT, anonymous)


# --- the stage --------------------------------------------------------------


def _seeded(conn: sqlite3.Connection, **fields: object) -> int:
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(conn, domain="example.ch", canonical_name="Example", **fields)
    db.upsert_membership(conn, segment_slug=SEGMENT.slug, company_id=company_id)
    conn.commit()
    return company_id


def test_geocode_skips_a_company_with_no_address(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seeded(conn)

    def fail(*args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("should not have made a request")

    monkeypatch.setattr(geocode, "geocode_query", fail)
    report = geocode.geocode(conn, SEGMENT, settings)

    assert report.skipped_no_address == 1
    assert report.geocoded == 0


def test_geocode_writes_coordinates(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = _seeded(conn, city="Zürich", canton="ZH")
    monkeypatch.setattr(
        geocode,
        "geocode_query",
        lambda *a, **k: GeoPoint(lat=47.3769, lon=8.5417, provider="swisstopo"),
    )

    report = geocode.geocode(conn, SEGMENT, settings)

    assert report.geocoded == 1
    row = conn.execute("SELECT lat, lon FROM company WHERE id = ?", (company_id,)).fetchone()
    assert row["lat"] == pytest.approx(47.3769)


def test_geocode_does_not_ask_twice_for_the_same_place(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two companies in one city is one geocoding request, not two."""
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    for domain in ("a.ch", "b.ch"):
        company_id = db.upsert_company(
            conn, domain=domain, canonical_name=domain, city="Zürich", canton="ZH"
        )
        db.upsert_membership(conn, segment_slug=SEGMENT.slug, company_id=company_id)
    conn.commit()

    calls: list[str] = []

    def counting(client: object, query: str, ua: str, **kwargs: object) -> GeoPoint:
        calls.append(query)
        return GeoPoint(lat=47.3769, lon=8.5417, provider="swisstopo")

    monkeypatch.setattr(geocode, "geocode_query", counting)
    report = geocode.geocode(conn, SEGMENT, settings)

    assert report.geocoded == 2
    assert len(calls) == 1, "the second company should have hit the in-run cache"


def test_geocode_skips_tier_three(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The long tail is not worth a request until somebody accepts it."""
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(
        conn, domain="noise.ch", canonical_name="Noise", city="Bern", canton="BE"
    )
    db.upsert_membership(
        conn, segment_slug=SEGMENT.slug, company_id=company_id, tier=3, tier_rationale="tail"
    )
    conn.commit()

    monkeypatch.setattr(
        geocode, "geocode_query", lambda *a, **k: GeoPoint(lat=46.9, lon=7.4, provider="x")
    )
    report = geocode.geocode(conn, SEGMENT, settings)
    assert report.considered == 0


def test_dry_run_writes_no_coordinates(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = _seeded(conn, city="Zürich", canton="ZH")
    monkeypatch.setattr(
        geocode, "geocode_query", lambda *a, **k: GeoPoint(lat=47.3, lon=8.5, provider="x")
    )

    report = geocode.geocode(conn, SEGMENT, settings, dry_run=True)

    assert report.geocoded == 1
    row = conn.execute("SELECT lat FROM company WHERE id = ?", (company_id,)).fetchone()
    assert row["lat"] is None


# --- the geocoder answers everything ----------------------------------------


@pytest.mark.parametrize(
    ("query", "label"),
    [
        # Every one of these is a real swisstopo response to a real query from
        # the database. Nothing failed; a company in Ontario was plotted in the
        # Alps, and the map lied with confidence.
        ("Toronto, Switzerland", "<b>Trient (VS)</b>"),
        ("Kochi, Switzerland", "<i>Ort</i> <b>Giesch</b> (VS) - Steg-Hohtenn"),
        ("Dortmund, Switzerland", "International Management Institute Switzerland IMI (LU) - Horw"),
        ("Calgary, Switzerland", "<b>Chalchegg</b> (AG)"),
    ],
)
def test_an_answer_unrelated_to_the_question_is_a_miss(query: str, label: str) -> None:
    assert not geocode.match_is_plausible(query, label)


@pytest.mark.parametrize(
    ("query", "label"),
    [
        ("Zürich, Switzerland", "<b>Zürich (ZH)</b>"),
        ("Lausanne, Switzerland", "<b>Lausanne (VD)</b>"),
        ("Zurich, Switzerland", "<b>Zürich (ZH)</b>"),
        ("St. Gallen, Switzerland", "<b>St. Gallen (SG)</b>"),
        ("Bahnhofstrasse 1, 8001, Zürich, Switzerland", "Bahnhofstrasse 1, 8001 Zürich"),
    ],
)
def test_a_real_match_survives(query: str, label: str) -> None:
    assert geocode.match_is_plausible(query, label)


def test_a_missing_label_is_not_a_match() -> None:
    assert not geocode.match_is_plausible("Zürich, Switzerland", None)


def test_a_canton_only_query_is_accepted() -> None:
    """No specific claim is being made beyond 'somewhere in this canton'."""
    assert geocode.match_is_plausible("Switzerland", "<b>Anywhere (VS)</b>")


def test_an_implausible_swisstopo_result_is_discarded(settings: Settings) -> None:
    """End to end: the provider answers, and the answer is thrown away."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "geo.admin.ch" in str(request.url):
            return httpx.Response(
                200, json={"results": [{"attrs": {"lat": 46.03, "lon": 6.99, "label": "Trient"}}]}
            )
        return httpx.Response(200, json=[])

    with _client(handler) as client:
        point = geocode.geocode_query(
            client, "Toronto, Switzerland", settings.user_agent(), last_nominatim=[0.0]
        )
    assert point is None, "a company in Ontario must not be given Swiss coordinates"
