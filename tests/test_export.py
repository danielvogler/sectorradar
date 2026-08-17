"""Exports and snapshots."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from sectorradar import db, export
from sectorradar.config import Segment

SEGMENT = Segment.model_validate(
    {
        "slug": "test-seg",
        "name": "Test",
        "geo": {"country": "CH"},
        "inclusion": "Include companies that build LLM agents for clients.",
        "tiers": {1: "primary"},
    }
)


def _company(
    conn: sqlite3.Connection,
    domain: str,
    *,
    tier: int | None = 1,
    lat: float | None = None,
    lon: float | None = None,
) -> int:
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(
        conn, domain=domain, canonical_name=domain, lat=lat, lon=lon, city="Zürich"
    )
    db.upsert_membership(
        conn,
        segment_slug=SEGMENT.slug,
        company_id=company_id,
        tier=tier,
        tier_rationale="because" if tier else None,
    )
    conn.commit()
    return company_id


def test_csv_export_has_a_header_and_a_row_per_company(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    _company(conn, "a.ch")
    _company(conn, "b.ch")

    path = export.export(conn, SEGMENT, tmp_path, fmt="csv")

    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {r["domain"] for r in rows} == {"a.ch", "b.ch"}
    assert "tier_rationale" in rows[0]


def test_geojson_omits_companies_without_coordinates(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Placing an unlocated company at (0, 0) would put it in the Gulf of Guinea."""
    _company(conn, "located.ch", lat=47.37, lon=8.54)
    _company(conn, "unlocated.ch")

    path = export.export(conn, SEGMENT, tmp_path, fmt="geojson")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 1
    feature = payload["features"][0]
    assert feature["geometry"]["coordinates"] == [8.54, 47.37]
    assert feature["properties"]["domain"] == "located.ch"


def test_geojson_puts_longitude_first(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """GeoJSON is [lon, lat]; getting this backwards is the classic silent bug."""
    _company(conn, "a.ch", lat=47.37, lon=8.54)
    path = export.export(conn, SEGMENT, tmp_path, fmt="geojson")
    lon, lat = json.loads(path.read_text(encoding="utf-8"))["features"][0]["geometry"][
        "coordinates"
    ]
    assert (lon, lat) == (8.54, 47.37)


def test_xlsx_export_is_written(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _company(conn, "a.ch")
    path = export.export(conn, SEGMENT, tmp_path, fmt="xlsx")
    assert path.exists()
    assert path.stat().st_size > 0


def test_json_export_round_trips(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _company(conn, "a.ch")
    path = export.export(conn, SEGMENT, tmp_path, fmt="json")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["domain"] == "a.ch"


def test_an_unknown_format_is_refused(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown export format"):
        export.export(conn, SEGMENT, tmp_path, fmt="pdf")


def test_accepted_only_filters(conn: sqlite3.Connection, tmp_path: Path) -> None:
    accepted = _company(conn, "yes.ch")
    _company(conn, "no.ch")
    db.set_review(
        conn,
        segment_slug=SEGMENT.slug,
        company_id=accepted,
        review_state="accepted",
        reviewed_by="owner",
    )

    rows = export.rows_for(conn, SEGMENT, accepted_only=True)
    assert [r["domain"] for r in rows] == ["yes.ch"]


# --- snapshots --------------------------------------------------------------


def test_snapshot_captures_the_tiered_set(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch", tier=1)
    _company(conn, "b.ch", tier=None)

    captured = export.snapshot(conn, SEGMENT)
    assert captured == 1

    stored = export.latest_snapshot(conn, SEGMENT)
    assert stored is not None
    assert stored[0]["domain"] == "a.ch"


def test_snapshot_keeps_rejections_too(conn: sqlite3.Connection) -> None:
    """Knowing a company was looked at and rejected is as useful as knowing it was kept."""
    company_id = _company(conn, "rejected.ch", tier=None)
    db.set_review(
        conn,
        segment_slug=SEGMENT.slug,
        company_id=company_id,
        review_state="rejected",
        reviewed_by="owner",
    )

    assert export.snapshot(conn, SEGMENT) == 1


def test_latest_snapshot_is_none_before_any_is_taken(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch")
    assert export.latest_snapshot(conn, SEGMENT) is None


def test_snapshots_accumulate(conn: sqlite3.Connection) -> None:
    """Change over time cannot be reconstructed later, so each one is kept."""
    _company(conn, "a.ch", tier=1)
    export.snapshot(conn, SEGMENT)
    _company(conn, "b.ch", tier=1)
    export.snapshot(conn, SEGMENT)

    count = conn.execute("SELECT COUNT(*) AS n FROM snapshot").fetchone()["n"]
    assert count == 2
    latest = export.latest_snapshot(conn, SEGMENT)
    assert latest is not None
    assert len(latest) == 2


def test_snapshot_dry_run_writes_nothing(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch", tier=1)
    export.snapshot(conn, SEGMENT, dry_run=True)
    assert conn.execute("SELECT COUNT(*) AS n FROM snapshot").fetchone()["n"] == 0
