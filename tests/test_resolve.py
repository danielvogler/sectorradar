"""Entity resolution — the stage that decides whether this project works.

Written before ``resolve.py`` existed. Every test below is one of the Swiss
traps from the specification, because the expensive failure here is not a crash
but a database that silently contains the same firm three times, or silently
merges two firms that are not the same.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorradar import db, resolve
from sectorradar.config import Segment

SEGMENT = Segment.model_validate(
    {
        "slug": "test-seg",
        "name": "Test market, Somewhere",
        "geo": {"country": "CH"},
        "inclusion": "Include companies that sell widgets as a named service.",
        "tiers": {1: "primary", 2: "secondary"},
    }
)


def _candidate(conn: sqlite3.Connection, name: str | None, url: str | None) -> int:
    return db.insert_candidate(
        conn,
        segment_slug=SEGMENT.slug,
        source="seeds",
        raw_name=name,
        raw_url=url,
        source_detail="test",
    )


def _companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM company ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# Domain normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.example.ch/services?utm=1", "example.ch"),
        ("http://EXAMPLE.CH", "example.ch"),
        ("example.ch", "example.ch"),
        ("https://example.ch/", "example.ch"),
        ("  https://www.Example.ch/de/ueber-uns  ", "example.ch"),
        ("https://sub.example.ch/", "sub.example.ch"),
    ],
)
def test_normalise_domain_strips_everything_but_the_host(raw: str, expected: str) -> None:
    assert resolve.normalise_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.linkedin.com/company/example",
        "https://facebook.com/example",
        "https://medium.com/@example",
        "https://example.github.io/",
        "https://de.wikipedia.org/wiki/Example",
        "https://twitter.com/example",
        "https://www.xing.com/companies/example",
    ],
)
def test_normalise_domain_rejects_non_company_hosts(raw: str) -> None:
    """A LinkedIn page is not a company website, and merging on one is wrong."""
    assert resolve.normalise_domain(raw) is None


@pytest.mark.parametrize("raw", [None, "", "   ", "not a url", "mailto:x@example.ch"])
def test_normalise_domain_rejects_junk(raw: str | None) -> None:
    assert resolve.normalise_domain(raw) is None


# ---------------------------------------------------------------------------
# Name normalisation — the legal-suffix trap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Brunner Consulting",
        "Brunner Consulting GmbH",
        "Brunner Consulting AG",
        "Brunner Consulting Sàrl",
        "Brunner Consulting SA",
        "Brunner Consulting Sagl",
        "Brunner Consulting Ltd.",
        "Brunner Consulting LLC",
        "  Brunner   Consulting  gmbh ",
        "BRUNNER CONSULTING AG",
    ],
)
def test_legal_suffixes_normalise_to_the_same_name(raw: str) -> None:
    """The same firm registered in three cantons must not become three rows."""
    assert resolve.normalise_name(raw) == "brunner consulting"


def test_normalise_name_keeps_a_meaningful_word_that_looks_like_a_suffix() -> None:
    """'Sagl' is a legal form; 'Sagler' is somebody's surname."""
    assert resolve.normalise_name("Sagler Analytics") == "sagler analytics"


def test_normalise_name_handles_an_empty_input() -> None:
    assert resolve.normalise_name(None) == ""
    assert resolve.normalise_name("   ") == ""


# ---------------------------------------------------------------------------
# Umlaut variants
# ---------------------------------------------------------------------------


def test_umlaut_variants_share_a_key() -> None:
    """Zürich / Zurich / Zuerich are one city, however a source spells it."""
    keys = [resolve.name_keys(n) for n in ("Zürich Data AG", "Zurich Data AG", "Zuerich Data AG")]
    assert keys[0] & keys[1]
    assert keys[0] & keys[2]
    assert keys[1] & keys[2]


def test_name_keys_covers_both_folding_conventions() -> None:
    """ü -> ue is the German convention; ü -> u is what careless sources emit."""
    keys = resolve.name_keys("Müller AI")
    assert "mueller ai" in keys
    assert "muller ai" in keys


def test_french_accents_fold_too() -> None:
    assert resolve.name_keys("Genève Intelligence") & resolve.name_keys("Geneve Intelligence")


def test_eszett_folds_to_ss() -> None:
    assert resolve.name_keys("Straßen KI") & resolve.name_keys("Strassen KI")


# ---------------------------------------------------------------------------
# Resolution behaviour
# ---------------------------------------------------------------------------


def test_two_candidates_on_one_domain_become_one_company(conn: sqlite3.Connection) -> None:
    _candidate(conn, "Example AG", "https://example.ch")
    _candidate(conn, "Example", "https://www.example.ch/services")
    conn.commit()

    report = resolve.resolve(conn, SEGMENT)

    assert len(_companies(conn)) == 1
    assert report.companies_created == 1
    assert report.merged_into_existing == 1


def test_multilingual_names_on_one_domain_merge(conn: sqlite3.Connection) -> None:
    """DE/FR/IT names for one entity never fuzzy-match; the domain is what unites them."""
    _candidate(conn, "Schweizerische Beratungsgesellschaft AG", "https://example.ch")
    _candidate(conn, "Société suisse de conseil SA", "https://example.ch")
    _candidate(conn, "Società svizzera di consulenza SA", "https://example.ch")
    conn.commit()

    resolve.resolve(conn, SEGMENT)
    assert len(_companies(conn)) == 1


def test_a_holding_and_its_operating_company_on_one_domain_merge(
    conn: sqlite3.Connection,
) -> None:
    """A deliberate limitation: one website is one row.

    Domain is the unique key, so a holding and its operating company that share
    a site collapse together. That is the right default for a market map — the
    thing being mapped is the business you can buy from, not the legal
    structure — but it is a real loss of information, so it is asserted rather
    than left to chance.
    """
    _candidate(conn, "Example Holding AG", "https://example.ch")
    _candidate(conn, "Example Services AG", "https://example.ch")
    conn.commit()

    resolve.resolve(conn, SEGMENT)
    assert len(_companies(conn)) == 1


def test_an_agency_and_its_product_spinoff_on_one_domain_merge(
    conn: sqlite3.Connection,
) -> None:
    _candidate(conn, "Acme Agency", "https://acme.ch")
    _candidate(conn, "AcmeBot", "https://acme.ch/product")
    conn.commit()

    resolve.resolve(conn, SEGMENT)
    assert len(_companies(conn)) == 1


def test_different_domains_stay_separate(conn: sqlite3.Connection) -> None:
    _candidate(conn, "Alpha AI", "https://alpha.ch")
    _candidate(conn, "Beta AI", "https://beta.ch")
    conn.commit()

    report = resolve.resolve(conn, SEGMENT)
    assert len(_companies(conn)) == 2
    assert report.companies_created == 2


def test_a_near_identical_name_on_another_domain_is_flagged_not_merged(
    conn: sqlite3.Connection,
) -> None:
    """Fuzzy name matches are a question for a human, never an automatic merge.

    Auto-merging on name similarity is how you silently lose a real competitor.
    """
    _candidate(conn, "Brunner Consulting GmbH", "https://brunner-consulting.ch")
    _candidate(conn, "Brunner Consulting AG", "https://brunnerconsulting.ch")
    conn.commit()

    report = resolve.resolve(conn, SEGMENT)

    assert len(_companies(conn)) == 2, "must not auto-merge on name similarity"
    assert report.flagged_duplicate == 1

    flagged = conn.execute(
        "SELECT review_state, review_note FROM membership WHERE review_state = 'needs_info'"
    ).fetchall()
    assert len(flagged) == 1
    assert "brunner-consulting.ch" in flagged[0]["review_note"]


def test_unrelated_names_are_not_flagged(conn: sqlite3.Connection) -> None:
    _candidate(conn, "Alpha Analytics", "https://alpha.ch")
    _candidate(conn, "Zeta Robotics", "https://zeta.ch")
    conn.commit()

    report = resolve.resolve(conn, SEGMENT)
    assert report.flagged_duplicate == 0


def test_candidates_with_no_usable_url_are_rejected_with_a_reason(
    conn: sqlite3.Connection,
) -> None:
    _candidate(conn, "Ghost Corp", None)
    _candidate(conn, "Social Only", "https://linkedin.com/company/social")
    conn.commit()

    report = resolve.resolve(conn, SEGMENT)

    assert report.rejected == 2
    assert len(_companies(conn)) == 0
    reasons = [r["reject_reason"] for r in conn.execute("SELECT reject_reason FROM candidate")]
    assert all(r for r in reasons), "every rejection must record why"


def test_resolve_links_the_candidate_to_the_company(conn: sqlite3.Connection) -> None:
    """Provenance: you must be able to ask which source produced a company."""
    _candidate(conn, "Example AG", "https://example.ch")
    conn.commit()

    resolve.resolve(conn, SEGMENT)

    row = conn.execute("SELECT resolved_to FROM candidate").fetchone()
    company = conn.execute("SELECT id FROM company").fetchone()
    assert row["resolved_to"] == company["id"]


def test_resolve_is_idempotent(conn: sqlite3.Connection) -> None:
    """Re-running a stage must not duplicate its own output."""
    _candidate(conn, "Example AG", "https://example.ch")
    _candidate(conn, "Alpha AI", "https://alpha.ch")
    conn.commit()

    resolve.resolve(conn, SEGMENT)
    first = len(_companies(conn))
    second_report = resolve.resolve(conn, SEGMENT)

    assert len(_companies(conn)) == first
    assert second_report.companies_created == 0


def test_resolve_creates_a_membership_row(conn: sqlite3.Connection) -> None:
    _candidate(conn, "Example AG", "https://example.ch")
    conn.commit()

    resolve.resolve(conn, SEGMENT)

    row = conn.execute(
        "SELECT segment_slug, review_state FROM membership WHERE segment_slug = ?",
        (SEGMENT.slug,),
    ).fetchone()
    assert row["segment_slug"] == SEGMENT.slug
    assert row["review_state"] == "pending"


def test_canonical_name_falls_back_to_the_domain(conn: sqlite3.Connection) -> None:
    _candidate(conn, None, "https://example-firm.ch")
    conn.commit()

    resolve.resolve(conn, SEGMENT)
    row = conn.execute("SELECT canonical_name FROM company").fetchone()
    assert "example-firm" in row["canonical_name"].lower()


def test_dry_run_writes_nothing(conn: sqlite3.Connection) -> None:
    _candidate(conn, "Example AG", "https://example.ch")
    conn.commit()

    report = resolve.resolve(conn, SEGMENT, dry_run=True)

    assert report.companies_created == 1, "the report still describes what would happen"
    assert len(_companies(conn)) == 0, "but nothing was written"
