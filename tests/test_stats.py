"""Coverage, saturation and cost reporting.

Gold-set recall is the number this project is judged on, so the behaviour that
matters most here is that it is computed honestly: normalised on both sides so
punctuation cannot understate it, and zero rather than a flattering default
when no gold set exists.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorradar import db, stats
from sectorradar.config import Segment


def _segment(gold: list[dict[str, object]] | None = None) -> Segment:
    return Segment.model_validate(
        {
            "slug": "test-seg",
            "name": "Test market, Somewhere",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that build LLM agents for clients.",
            "tiers": {1: "primary", 2: "secondary"},
            "gold_set": gold or [],
        }
    )


def _company(conn: sqlite3.Connection, domain: str, *, tier: int | None = None) -> int:
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    company_id = db.upsert_company(conn, domain=domain, canonical_name=domain)
    db.upsert_membership(
        conn,
        segment_slug="test-seg",
        company_id=company_id,
        tier=tier,
        tier_rationale="because" if tier else None,
    )
    conn.commit()
    return company_id


# --- recall -----------------------------------------------------------------


def test_recall_counts_what_was_found(conn: sqlite3.Connection) -> None:
    segment = _segment([{"domain": "a.ch"}, {"domain": "b.ch"}, {"domain": "c.ch"}])
    _company(conn, "a.ch")
    _company(conn, "b.ch")

    recall = stats.gold_set_recall(conn, segment)
    assert recall.expected == 3
    assert recall.found == 2
    assert recall.percent == pytest.approx(66.7, abs=0.1)
    assert recall.missing == ("c.ch",)


def test_recall_normalises_both_sides(conn: sqlite3.Connection) -> None:
    """A gold entry written as a full URL must match a stored bare domain."""
    segment = _segment([{"domain": "https://www.Example.ch/"}])
    _company(conn, "example.ch")

    recall = stats.gold_set_recall(conn, segment)
    assert recall.found == 1
    assert recall.percent == 100.0


def test_recall_with_no_gold_set_is_zero_not_perfect(conn: sqlite3.Connection) -> None:
    """An empty gold set must never read as full coverage."""
    _company(conn, "a.ch")
    recall = stats.gold_set_recall(conn, _segment([]))
    assert recall.expected == 0
    assert recall.ratio == 0.0
    assert recall.percent == 0.0


def test_recall_ignores_companies_in_another_segment(conn: sqlite3.Connection) -> None:
    segment = _segment([{"domain": "a.ch"}])
    db.upsert_segment(conn, "other", "Other", "slug: other")
    company_id = db.upsert_company(conn, domain="a.ch", canonical_name="a.ch")
    db.upsert_membership(conn, segment_slug="other", company_id=company_id)
    conn.commit()

    assert stats.gold_set_recall(conn, segment).found == 0


# --- collection -------------------------------------------------------------


def test_collect_counts_by_tier_and_review(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch", tier=1)
    _company(conn, "b.ch", tier=1)
    _company(conn, "c.ch", tier=3)
    _company(conn, "d.ch")

    collected = stats.collect(conn, _segment())
    assert collected.companies == 4
    assert collected.by_tier == {"1": 2, "3": 1, "unclassified": 1}
    assert collected.by_review == {"pending": 4}


def test_collect_counts_only_tier_one_and_two_rationales(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch", tier=1)
    _company(conn, "b.ch", tier=2)
    _company(conn, "c.ch", tier=3)

    assert stats.collect(conn, _segment()).with_rationale == 2


def test_source_yield_is_new_over_results(conn: sqlite3.Connection) -> None:
    """The ratio that says whether a channel is worth querying again."""
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    conn.execute(
        """
        INSERT INTO discovery_run
          (segment_slug, source, results_n, new_unique_n, cost_usd, started_at, finished_at)
        VALUES ('test-seg', 'websearch', 100, 25, 0.5, '2026-01-01', '2026-01-01')
        """
    )
    conn.commit()

    collected = stats.collect(conn, _segment())
    assert len(collected.sources) == 1
    assert collected.sources[0].yield_ratio == pytest.approx(0.25)
    assert collected.total_cost_usd == pytest.approx(0.5)


def test_a_source_with_no_results_has_no_yield(conn: sqlite3.Connection) -> None:
    """Division by zero here would crash the one command you run to check health."""
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    conn.execute(
        """
        INSERT INTO discovery_run
          (segment_slug, source, results_n, new_unique_n, started_at, finished_at)
        VALUES ('test-seg', 'directories', 0, 0, '2026-01-01', '2026-01-01')
        """
    )
    conn.commit()
    assert stats.collect(conn, _segment()).sources[0].yield_ratio == 0.0


def test_rejected_candidates_are_reported(conn: sqlite3.Connection) -> None:
    """LINDAS produces rows with no website; the count makes that visible."""
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    db.insert_candidate(
        conn,
        segment_slug="test-seg",
        source="lindas",
        raw_name="X AG",
        raw_url=None,
        source_detail="purpose sweep",
    )
    conn.execute("UPDATE candidate SET reject_reason = 'no usable URL'")
    conn.commit()

    collected = stats.collect(conn, _segment())
    assert collected.candidates == 1
    assert collected.rejected_candidates == 1


# --- the report -------------------------------------------------------------


def test_report_says_so_when_there_is_no_gold_set(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch")
    text = stats.format_report(stats.collect(conn, _segment()))
    assert "no gold set defined" in text


def test_report_lists_what_was_not_found(conn: sqlite3.Connection) -> None:
    segment = _segment([{"domain": "found.ch"}, {"domain": "missing.ch"}])
    _company(conn, "found.ch")
    text = stats.format_report(stats.collect(conn, segment))
    assert "missing.ch" in text
    assert "50.0%" in text


# --- the honesty of the headline number -------------------------------------


def _segment_with_seeds(gold: list[str], seeds: list[str]) -> Segment:
    return Segment.model_validate(
        {
            "slug": "test-seg",
            "name": "Test market, Somewhere",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that build LLM agents for clients.",
            "tiers": {1: "primary"},
            "sources": {"seeds": {"enabled": True, "urls": [f"https://{d}" for d in seeds]}},
            "gold_set": [{"domain": d} for d in gold],
        }
    )


def test_blind_recall_excludes_seeded_entries(conn: sqlite3.Connection) -> None:
    """A gold entry that was seeded is in the database by definition, not by discovery."""
    segment = _segment_with_seeds(gold=["a.ch", "b.ch", "c.ch", "d.ch"], seeds=["a.ch", "b.ch"])
    for domain in ("a.ch", "b.ch", "c.ch"):
        _company(conn, domain)

    recall = stats.gold_set_recall(conn, segment)
    assert recall.percent == 75.0, "headline counts everything"
    assert recall.blind_expected == 2, "only c.ch and d.ch were never seeded"
    assert recall.blind_found == 1
    assert recall.blind_percent == 50.0


def test_a_gold_set_drawn_from_the_seed_list_is_flagged(conn: sqlite3.Connection) -> None:
    """A near-100% headline built from seeds must announce itself as meaningless."""
    segment = _segment_with_seeds(gold=["a.ch", "b.ch", "c.ch"], seeds=["a.ch", "b.ch", "c.ch"])
    for domain in ("a.ch", "b.ch", "c.ch"):
        _company(conn, domain)

    recall = stats.gold_set_recall(conn, segment)
    assert recall.percent == 100.0
    assert recall.is_mostly_seeded

    text = stats.format_report(stats.collect(conn, segment))
    assert "largely measures the seed list" in text


def test_an_independent_gold_set_is_not_flagged(conn: sqlite3.Connection) -> None:
    segment = _segment_with_seeds(gold=["a.ch", "b.ch", "c.ch"], seeds=["z.ch"])
    _company(conn, "a.ch")
    recall = stats.gold_set_recall(conn, segment)
    assert not recall.is_mostly_seeded


def test_blind_percent_is_zero_when_every_gold_entry_was_seeded(
    conn: sqlite3.Connection,
) -> None:
    """No blind entries means no measurable coverage, not perfect coverage."""
    segment = _segment_with_seeds(gold=["a.ch"], seeds=["a.ch"])
    _company(conn, "a.ch")
    assert stats.gold_set_recall(conn, segment).blind_percent == 0.0
