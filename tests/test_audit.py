"""The checks that replace somebody noticing.

Every gap this tool has had was found by a person looking at output and saying
"that can't be right" — one company in Basel, ninety-nine without an address,
a hundred and forty-eight doing retrieval augmentation. The fixes are worth
little if the next person to run it has to make the same observations, so these
tests fix the behaviour that does the noticing.

The property that matters most is restraint. A report that fires on a healthy
dataset teaches people to skim past it, at which point it protects nothing.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorradar import audit, db
from sectorradar.config import Segment


def _segment(**kwargs: object) -> Segment:
    base: dict[str, object] = {
        "slug": "test-seg",
        "name": "Test market, Somewhere",
        "geo": {"country": "CH"},
        "inclusion": "Include companies that build LLM agents for clients.",
        "tiers": {1: "primary", 2: "secondary"},
        "facets": {"service_type": ["agent_dev", "workshops"]},
        "gold_set": [{"domain": "known.ch", "expected_tier": 1}],
    }
    base.update(kwargs)
    return Segment.model_validate(base)


def _company(
    conn: sqlite3.Connection,
    domain: str,
    *,
    tier: int | None = 1,
    canton: str | None = "ZH",
    city: str | None = "Zürich",
    lat: float | None = 47.37,
) -> int:
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    company_id = db.upsert_company(conn, domain=domain, canonical_name=domain, canton=canton)
    conn.execute(
        "UPDATE company SET city = ?, lat = ?, lon = ? WHERE id = ?", (city, lat, 8.5, company_id)
    )
    db.upsert_membership(
        conn, segment_slug="test-seg", company_id=company_id, tier=tier, tier_rationale="x"
    )
    conn.commit()
    return company_id


def _page(conn: sqlite3.Connection, company_id: int, url: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO page (url_sha, company_id, url, fetched_at, path)
           VALUES (?, ?, ?, '2026-01-01', '/tmp/x')""",
        (f"{company_id}-{url}", company_id, url),
    )
    conn.commit()


def _offering(conn: sqlite3.Connection, company_id: int) -> None:
    conn.execute(
        """INSERT INTO offering (company_id, label, evidence_url, evidence_quote, extracted_at)
           VALUES (?, 'a service', 'https://x/', 'q', '2026-01-01')""",
        (company_id,),
    )
    conn.commit()


def _discovery(
    conn: sqlite3.Connection, source: str, results: int, new: int, at: str = "2026-01-01T10:00:00"
) -> None:
    conn.execute(
        """INSERT INTO discovery_run
             (segment_slug, source, query, results_n, new_unique_n, started_at, finished_at)
           VALUES ('test-seg', ?, 'q', ?, ?, ?, ?)""",
        (source, results, new, at, at),
    )
    conn.commit()


def _areas(report: audit.AuditReport) -> set[str]:
    return {f.area for f in report.findings}


def test_an_empty_segment_says_so_first_and_stops(conn: sqlite3.Connection) -> None:
    """No point reporting thin vocabulary coverage when nothing was classified."""
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    report = audit.audit(conn, _segment())

    assert len(report.findings) == 1
    assert report.findings[0].severity == "high"
    assert "no companies" in report.findings[0].what


def test_an_address_that_was_never_geocoded_is_flagged_as_free_to_fix(
    conn: sqlite3.Connection,
) -> None:
    company_id = _company(conn, "a.ch", lat=None)
    conn.execute("UPDATE company SET geocode_status = NULL WHERE id = ?", (company_id,))
    conn.commit()

    report = audit.audit(conn, _segment())
    finding = next(f for f in report.findings if f.area == "address")

    assert finding.severity == "high"
    assert "geocode" in finding.do


def test_a_known_company_that_was_never_found_is_the_loudest_finding(
    conn: sqlite3.Connection,
) -> None:
    """Recall against the gold set is the only measure of discovery there is."""
    _company(conn, "other.ch")

    report = audit.audit(conn, _segment())
    finding = next(f for f in report.findings if f.area == "gold set")

    assert finding.severity == "high"
    assert "known.ch" in finding.detail


def test_a_known_company_found_and_then_excluded_is_reported_separately(
    conn: sqlite3.Connection,
) -> None:
    """A different problem from not finding it, with a different fix."""
    _company(conn, "known.ch", tier=None)
    _company(conn, "other.ch")

    report = audit.audit(conn, _segment())
    findings = [f for f in report.findings if f.area == "gold set"]

    assert any("classified out" in f.what for f in findings)


def test_a_segment_with_no_gold_set_is_told_discovery_is_unmeasured(
    conn: sqlite3.Connection,
) -> None:
    _company(conn, "a.ch")

    report = audit.audit(conn, _segment(gold_set=[]))
    finding = next(f for f in report.findings if f.area == "gold set")

    assert finding.severity == "high"
    assert "unmeasured" in finding.what


def test_declared_values_that_were_never_applied_point_at_the_evidence_words(
    conn: sqlite3.Connection,
) -> None:
    """The usual cause is language, not absence: `seo` misses Suchmaschinenoptimierung."""
    company_id = _company(conn, "known.ch")
    _offering(conn, company_id)
    conn.execute(
        "INSERT INTO tag (company_id, facet, value) VALUES (?, 'service_type', 'agent_dev')",
        (company_id,),
    )
    conn.commit()

    report = audit.audit(conn, _segment())
    finding = next(f for f in report.findings if f.area == "vocabulary")

    assert "workshops" in finding.detail
    assert "evidence words" in finding.do


def test_a_healthy_dataset_produces_no_high_severity_findings(conn: sqlite3.Connection) -> None:
    """The property that matters most: a report that always fires protects nothing."""
    for index in range(12):
        company_id = _company(conn, f"c{index}.ch" if index else "known.ch")
        _offering(conn, company_id)
        _page(conn, company_id, f"https://c{index}.ch/referenzen")
        _page(conn, company_id, f"https://c{index}.ch/news")
        for value in ("agent_dev", "workshops"):
            conn.execute(
                "INSERT INTO tag (company_id, facet, value) VALUES (?, 'service_type', ?)",
                (company_id, value),
            )
        conn.execute(
            """INSERT INTO case_study
                 (company_id, title, industry, summary, evidence_url, evidence_quote, extracted_at)
               VALUES (?, 't', 'finance', '', 'https://x/', 'q', '2026-01-01')""",
            (company_id,),
        )
    conn.commit()
    # A healthy dataset is one where discovery has been run and has stopped
    # producing, not one where it was never started.
    _discovery(conn, "websearch", results=300, new=1)

    report = audit.audit(conn, _segment())

    assert [f for f in report.findings if f.severity == "high"] == []


def test_every_finding_says_what_to_do_about_it(conn: sqlite3.Connection) -> None:
    """A report that only says what is wrong makes somebody else do the thinking."""
    _company(conn, "a.ch", lat=None)

    for finding in audit.audit(conn, _segment()).findings:
        assert finding.do.strip(), finding.what
        assert finding.what.strip()


def test_findings_are_ranked_with_the_worst_first(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch", lat=None)

    ranked = audit.audit(conn, _segment()).ranked
    severities = [f.severity for f in ranked]

    assert severities == sorted(severities, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])


@pytest.mark.parametrize("country", ["DE", "AT", "US"])
def test_the_swiss_canton_check_stays_out_of_other_countries(
    conn: sqlite3.Connection, country: str
) -> None:
    """Nothing here may assume Switzerland; a segment is any market."""
    _company(conn, "known.ch", canton=None)

    report = audit.audit(conn, _segment(geo={"country": country}))

    assert "geography" not in _areas(report)


# --- discovery saturation ----------------------------------------------------


def test_a_pass_still_finding_companies_says_keep_looking(conn: sqlite3.Connection) -> None:
    """The distinction nobody makes by eye: exhausted, or merely stopped.

    A run returning a quarter of its results as companies never seen before has
    not finished the market — it was switched off partway, and the answer is
    short by an unknown amount.
    """
    _company(conn, "known.ch")
    _discovery(conn, "websearch", results=400, new=108)

    report = audit.audit(conn, _segment())
    finding = next(f for f in report.findings if f.area == "discovery")

    assert finding.severity == "high"
    assert "deepen" in finding.do
    assert "27%" in finding.detail


def test_an_exhausted_channel_is_told_to_change_kind_not_quantity(
    conn: sqlite3.Connection,
) -> None:
    _company(conn, "known.ch")
    _discovery(conn, "websearch", results=400, new=2)

    report = audit.audit(conn, _segment())
    finding = next(f for f in report.findings if f.area == "discovery")

    assert finding.severity == "low"
    assert "different source" in finding.do


def test_only_the_most_recent_pass_counts(conn: sqlite3.Connection) -> None:
    """Early passes were productive by definition — the dataset was emptier."""
    _company(conn, "known.ch")
    _discovery(conn, "websearch", results=200, new=180, at="2026-01-01T10:00:00")
    _discovery(conn, "websearch", results=400, new=3, at="2026-06-01T10:00:00")

    report = audit.audit(conn, _segment())
    discovery = [f for f in report.findings if f.area == "discovery"]

    assert not any("still finding" in f.what for f in discovery)


def test_a_tiny_pass_is_not_used_to_judge_saturation(conn: sqlite3.Connection) -> None:
    """Five results say nothing about whether a market is exhausted."""
    _company(conn, "known.ch")
    _discovery(conn, "websearch", results=6, new=6)

    report = audit.audit(conn, _segment())

    assert not [f for f in report.findings if f.area == "discovery"]


def test_a_segment_that_never_ran_discovery_is_told_so_loudly(
    conn: sqlite3.Connection,
) -> None:
    _company(conn, "known.ch")

    report = audit.audit(conn, _segment())
    finding = next(f for f in report.findings if f.area == "discovery")

    assert finding.severity == "high"
    assert "no discovery" in finding.what


def test_a_canton_scoped_segment_is_not_judged_against_the_country(
    conn: sqlite3.Connection,
) -> None:
    """A market that says `cantons: [ZH]` has not failed by being only in ZH.

    Unscoped, this produced fifteen findings on a correct single-canton run,
    each recommending queries for a region the segment deliberately excludes —
    advice that damages the dataset if followed. A noisy audit is an unread
    audit, and a confidently wrong one is worse than silence.
    """
    segment = _segment(geo={"country": "CH", "cantons": ["ZH"]})
    for index in range(12):
        company_id = _company(conn, "known.ch" if index == 0 else f"c{index}.ch", canton="ZH")
        _offering(conn, company_id)
    conn.commit()

    report = audit.audit(conn, segment)

    assert "geography" not in _areas(report)


def test_a_segment_covering_several_cantons_is_judged_against_those(
    conn: sqlite3.Connection,
) -> None:
    """Scoping must narrow the comparison, not switch the check off."""
    segment = _segment(geo={"country": "CH", "cantons": ["ZH", "BE", "VD"]})
    for index in range(20):
        company_id = _company(conn, "known.ch" if index == 0 else f"c{index}.ch", canton="ZH")
        _offering(conn, company_id)
    conn.commit()

    report = audit.audit(conn, segment)
    finding = next(f for f in report.findings if f.area == "geography")

    # BE and VD are in scope and empty; nothing outside the segment is named.
    assert "BE" in finding.detail
    assert "TI" not in finding.detail
    assert "GE" not in finding.detail


def test_a_nationwide_segment_still_gets_the_whole_country(
    conn: sqlite3.Connection,
) -> None:
    segment = _segment(geo={"country": "CH"})
    for index in range(20):
        company_id = _company(conn, "known.ch" if index == 0 else f"c{index}.ch", canton="ZH")
        _offering(conn, company_id)
    conn.commit()

    report = audit.audit(conn, segment)
    finding = next(f for f in report.findings if f.area == "geography")

    assert "BE" in finding.detail
