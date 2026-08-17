"""Schema, migrations and the write helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sectorradar import db

EXPECTED_TABLES = {
    "segment",
    "company",
    "membership",
    "company_field",
    "offering",
    "tag",
    "candidate",
    "discovery_run",
    "page",
    "snapshot",
    "schema_version",
    "company_fts",
}

EXPECTED_INDEXES = {
    "idx_company_domain",
    "idx_company_canton",
    "idx_membership_lookup",
    "idx_field_lookup",
    "idx_tag_facet",
    "idx_candidate_segment",
    "idx_page_company",
}


def test_init_creates_every_table(conn: sqlite3.Connection) -> None:
    assert db.table_names(conn) >= EXPECTED_TABLES


def test_init_creates_every_index(conn: sqlite3.Connection) -> None:
    assert db.index_names(conn) >= EXPECTED_INDEXES


def test_init_reports_the_current_schema_version(conn: sqlite3.Connection) -> None:
    assert db.current_version(conn) == db.SCHEMA_VERSION


def test_init_is_idempotent(db_path: Path) -> None:
    """Re-running migrations applies nothing and changes nothing."""
    applied_again = db.init_db(db_path)
    assert applied_again == 0

    with db.connect(db_path) as conn:
        assert db.current_version(conn) == db.SCHEMA_VERSION
        rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
        assert rows["n"] == len(db.MIGRATIONS)


def test_migrating_a_fresh_database_applies_every_step(tmp_path: Path) -> None:
    applied = db.init_db(tmp_path / "fresh.db")
    assert applied == len(db.MIGRATIONS)


def test_read_only_connection_refuses_to_write(db_path: Path) -> None:
    with db.connect(db_path, read_only=True) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "INSERT INTO segment (slug, name, config_yaml, created_at) VALUES ('x','x','x','x')"
        )


def test_read_only_connection_on_a_missing_database_says_what_to_run(tmp_path: Path) -> None:
    with (
        pytest.raises(FileNotFoundError, match="sectorradar init"),
        db.connect(tmp_path / "nope.db", read_only=True),
    ):
        pass  # pragma: no cover


def test_upsert_company_is_keyed_on_domain(conn: sqlite3.Connection) -> None:
    first = db.upsert_company(conn, domain="example.ch", canonical_name="Example")
    second = db.upsert_company(conn, domain="example.ch", canonical_name="Example AG")
    assert first == second
    assert conn.execute("SELECT COUNT(*) AS n FROM company").fetchone()["n"] == 1


def test_upsert_company_does_not_blank_known_values(conn: sqlite3.Connection) -> None:
    """A later stage with partial information must not erase an earlier one's work."""
    company_id = db.upsert_company(
        conn, domain="example.ch", canonical_name="Example", city="Zurich"
    )
    db.upsert_company(conn, domain="example.ch", canonical_name="Example", canton="ZH")

    row = conn.execute("SELECT city, canton FROM company WHERE id = ?", (company_id,)).fetchone()
    assert row["city"] == "Zurich"
    assert row["canton"] == "ZH"


def test_upsert_company_rejects_an_unknown_column(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown column"):
        db.upsert_company(conn, domain="example.ch", canonical_name="Example", revenue=1)


def test_upsert_membership_preserves_a_human_review(conn: sqlite3.Connection) -> None:
    db.upsert_segment(conn, "seg", "Segment", "slug: seg")
    company_id = db.upsert_company(conn, domain="example.ch", canonical_name="Example")
    db.upsert_membership(conn, segment_slug="seg", company_id=company_id, tier=2)
    db.set_review(
        conn,
        segment_slug="seg",
        company_id=company_id,
        review_state="accepted",
        reviewed_by="dv",
        review_note="clearly tier 1",
        tier=1,
    )

    # A later classify run should not silently undo the human's decision.
    db.upsert_membership(conn, segment_slug="seg", company_id=company_id, tier=3)
    row = conn.execute(
        "SELECT tier, review_state, review_note FROM membership WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    assert row["review_state"] == "accepted"
    assert row["review_note"] == "clearly tier 1"
    # COALESCE keeps the reviewed tier because the human set it explicitly.
    assert row["tier"] == 1


def test_insert_candidate_records_provenance(conn: sqlite3.Connection) -> None:
    candidate_id = db.insert_candidate(
        conn,
        segment_slug="seg",
        source="websearch",
        raw_name="Example",
        raw_url="https://example.ch",
        source_detail="query: AI Beratung",
    )
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    assert row["source"] == "websearch"
    assert row["source_detail"] == "query: AI Beratung"
    assert row["discovered_at"]


def test_upsert_segment_stores_the_yaml_it_ran_with(conn: sqlite3.Connection) -> None:
    db.upsert_segment(conn, "seg", "Segment", "slug: seg\nname: Segment")
    db.upsert_segment(conn, "seg", "Renamed", "slug: seg\nname: Renamed")
    row = conn.execute("SELECT name, config_yaml FROM segment WHERE slug = 'seg'").fetchone()
    assert row["name"] == "Renamed"
    assert "Renamed" in row["config_yaml"]


def test_upsert_membership_still_updates_an_unreviewed_row(conn: sqlite3.Connection) -> None:
    """Preserving reviewed rows must not freeze the pending ones too."""
    db.upsert_segment(conn, "seg", "Segment", "slug: seg")
    company_id = db.upsert_company(conn, domain="example.ch", canonical_name="Example")
    db.upsert_membership(conn, segment_slug="seg", company_id=company_id, tier=3)
    db.upsert_membership(
        conn, segment_slug="seg", company_id=company_id, tier=1, tier_rationale="reclassified"
    )

    row = conn.execute(
        "SELECT tier, tier_rationale FROM membership WHERE company_id = ?", (company_id,)
    ).fetchone()
    assert row["tier"] == 1
    assert row["tier_rationale"] == "reclassified"
