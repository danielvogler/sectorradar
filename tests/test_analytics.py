"""Aggregates over a segment.

The behaviour that matters most: the operator's own companies are in the
dataset but never in the baseline they are compared against. An average that
quietly includes you tells you that you are average.
"""

from __future__ import annotations

import sqlite3

import pytest

from sectorradar import analytics, db
from sectorradar.config import Segment


def _segment(own: list[str] | None = None) -> Segment:
    return Segment.model_validate(
        {
            "slug": "test-seg",
            "name": "Test market, Somewhere",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that build LLM agents for clients.",
            "tiers": {1: "primary", 2: "secondary"},
            "own_domains": own or [],
        }
    )


def _company(
    conn: sqlite3.Connection,
    domain: str,
    *,
    tier: int | None = 1,
    headcount: int | None = None,
    canton: str | None = "ZH",
) -> int:
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    company_id = db.upsert_company(
        conn, domain=domain, canonical_name=domain, headcount_est=headcount, canton=canton
    )
    db.upsert_membership(
        conn, segment_slug="test-seg", company_id=company_id, tier=tier, tier_rationale="x"
    )
    conn.commit()
    return company_id


@pytest.mark.parametrize(
    ("headcount", "band"),
    [(None, "unknown"), (1, "1-9"), (9, "1-9"), (10, "10-49"), (120, "50-249"), (5000, "250+")],
)
def test_size_bands(headcount: int | None, band: str) -> None:
    assert analytics.band_for(headcount) == band


def test_own_companies_are_excluded_from_the_baseline(conn: sqlite3.Connection) -> None:
    """The whole point: you are in the dataset, not in the average."""
    _company(conn, "mine.ch", headcount=3)
    _company(conn, "rival-a.ch", headcount=100)
    _company(conn, "rival-b.ch", headcount=100)

    stats = analytics.collect(conn, _segment(own=["mine.ch"]))

    assert stats.companies == 3, "own company is still counted in the dataset"
    assert stats.own == 1
    assert stats.compared == 2, "but not in what it is compared against"

    bands = {c.label: c.n for c in stats.by_size}
    assert "1-9" not in bands, "the own company's band must not appear in the field"
    assert bands["50-249"] == 2


def test_without_own_domains_everything_is_compared(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch")
    _company(conn, "b.ch")
    stats = analytics.collect(conn, _segment())
    assert stats.own == 0
    assert stats.compared == 2


def test_an_empty_segment_does_not_divide_by_zero(conn: sqlite3.Connection) -> None:
    """Shares and averages are where an empty set becomes an exception."""
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")
    conn.commit()
    stats = analytics.collect(conn, _segment())
    assert stats.companies == 0
    assert stats.by_tier == []
    assert stats.to_dict()["segment"] == "test-seg"


def test_a_segment_that_is_entirely_own_companies(conn: sqlite3.Connection) -> None:
    """compared == 0, which is the other divide-by-zero."""
    _company(conn, "mine.ch")
    stats = analytics.collect(conn, _segment(own=["mine.ch"]))
    assert stats.compared == 0
    assert all(c.share <= 1.0 for c in stats.by_tier)


def test_shares_sum_to_about_one(conn: sqlite3.Connection) -> None:
    for i in range(4):
        _company(conn, f"c{i}.ch", canton="ZH" if i < 3 else "BE")
    stats = analytics.collect(conn, _segment())
    assert sum(c.share for c in stats.by_canton) == pytest.approx(1.0, abs=0.01)


def test_industry_coverage_separates_claimed_from_evidenced(conn: sqlite3.Connection) -> None:
    """A sector on a list and a sector with a client in it are different claims."""
    claimed = _company(conn, "claims.ch")
    evidenced = _company(conn, "proves.ch")

    conn.execute(
        "INSERT INTO tag (company_id, facet, value) VALUES (?, 'vertical', 'pharma')", (claimed,)
    )
    conn.execute(
        "INSERT INTO tag (company_id, facet, value) VALUES (?, 'vertical', 'pharma')", (evidenced,)
    )
    conn.execute(
        """INSERT INTO client_reference
             (company_id, client_name, industry, evidence_url, evidence_quote, extracted_at)
           VALUES (?, 'Acme Pharma AG', 'pharma', 'https://x', 'q', '2026-01-01')""",
        (evidenced,),
    )
    conn.commit()

    pharma = next(
        i for i in analytics.collect(conn, _segment()).industries if i.industry == "pharma"
    )
    assert pharma.providers == 2
    assert pharma.with_evidence == 1


def test_industries_are_ranked_by_provider_count(conn: sqlite3.Connection) -> None:
    """The thin end of this list is the part worth looking at."""
    for i in range(3):
        cid = _company(conn, f"f{i}.ch")
        conn.execute(
            "INSERT INTO tag (company_id, facet, value) VALUES (?, 'vertical', 'finance')", (cid,)
        )
    rare = _company(conn, "rare.ch")
    conn.execute(
        "INSERT INTO tag (company_id, facet, value) VALUES (?, 'vertical', 'agriculture')", (rare,)
    )
    conn.commit()

    industries = analytics.collect(conn, _segment()).industries
    assert industries[0].industry == "finance"
    assert industries[-1].industry == "agriculture"


def test_site_signals_are_reported_by_size_band(conn: sqlite3.Connection) -> None:
    """What bigger firms publish that smaller ones do not."""
    small = _company(conn, "small.ch", headcount=4)
    large = _company(conn, "large.ch", headcount=300)
    for cid, present in ((small, 0), (large, 1)):
        conn.execute(
            """INSERT INTO site_signal (company_id, signal, present, extracted_at)
               VALUES (?, 'case_studies', ?, '2026-01-01')""",
            (cid, present),
        )
    conn.commit()

    signal = next(
        s for s in analytics.collect(conn, _segment()).signals if s.signal == "case_studies"
    )
    assert signal.by_band["1-9"] == 0.0
    assert signal.by_band["250+"] == 1.0
    assert signal.overall == pytest.approx(0.5)


def test_own_versus_field_keeps_them_apart(conn: sqlite3.Connection) -> None:
    mine = _company(conn, "mine.ch")
    _company(conn, "rival.ch")
    conn.execute(
        """INSERT INTO offering (company_id, label, evidence_url, evidence_quote, extracted_at)
           VALUES (?, 'Agents', 'https://x', 'q', '2026-01-01')""",
        (mine,),
    )
    conn.commit()

    result = analytics.own_versus_field(conn, _segment(own=["mine.ch"]))
    assert [o["domain"] for o in result["own"]] == ["mine.ch"]
    assert result["field"]["companies"] == 1
    assert result["field"]["totals"]["offerings"] == 0, "own offerings excluded from the field"


def test_own_versus_field_without_own_domains_says_so(conn: sqlite3.Connection) -> None:
    _company(conn, "a.ch")
    result = analytics.own_versus_field(conn, _segment())
    assert result["own"] == []
    assert "no own_domains" in result["note"]


# --- shapes the front end depends on ----------------------------------------


def test_analytics_serialises_to_json(conn: sqlite3.Connection) -> None:
    """The web build embeds this directly; anything unserialisable is a crash."""
    import json

    _company(conn, "a.ch", headcount=12)
    payload = json.dumps(analytics.collect(conn, _segment()).to_dict())
    assert json.loads(payload)["segment"] == "test-seg"


def test_size_bands_are_ordered_smallest_first(conn: sqlite3.Connection) -> None:
    """A chart reading straight from this should not have to sort it."""
    for i, headcount in enumerate((400, 3, 80, 20)):
        _company(conn, f"c{i}.ch", headcount=headcount)
    labels = [c.label for c in analytics.collect(conn, _segment()).by_size]
    assert labels == ["1-9", "10-49", "50-249", "250+"]


def test_unknown_size_sorts_last(conn: sqlite3.Connection) -> None:
    _company(conn, "known.ch", headcount=5)
    _company(conn, "unknown.ch", headcount=None)
    labels = [c.label for c in analytics.collect(conn, _segment()).by_size]
    assert labels[-1] == "unknown"


def test_totals_count_every_kind_of_evidence(conn: sqlite3.Connection) -> None:
    company_id = _company(conn, "a.ch")
    conn.execute(
        """INSERT INTO offering (company_id, label, evidence_url, evidence_quote, extracted_at)
           VALUES (?, 'x', 'u', 'q', 'now')""",
        (company_id,),
    )
    conn.execute(
        """INSERT INTO case_study
             (company_id, title, evidence_url, evidence_quote, extracted_at)
           VALUES (?, 't', 'u', 'q', 'now')""",
        (company_id,),
    )
    conn.execute(
        """INSERT INTO product (company_id, name, evidence_url, evidence_quote, extracted_at)
           VALUES (?, 'p', 'u', 'q', 'now')""",
        (company_id,),
    )
    conn.commit()

    totals = analytics.collect(conn, _segment()).totals
    assert totals["offerings"] == 1
    assert totals["case_studies"] == 1
    assert totals["products"] == 1
    assert totals["clients"] == 0


def test_a_company_in_two_segments_is_counted_once_per_segment(
    conn: sqlite3.Connection,
) -> None:
    """Membership is per segment, so an aggregate must not leak across them."""
    company_id = _company(conn, "shared.ch")
    db.upsert_segment(conn, "other-seg", "Other", "slug: other-seg")
    db.upsert_membership(conn, segment_slug="other-seg", company_id=company_id, tier=1)
    conn.commit()

    assert analytics.collect(conn, _segment()).companies == 1


def test_industry_providers_are_never_fewer_than_the_companies_with_evidence(
    conn: sqlite3.Connection,
) -> None:
    """A company with a case study in a sector is a provider in that sector.

    Counting `providers` from the `vertical` tag alone made this false —
    `legal` reported 5 providers and 7 with evidence — and the chart drew the
    difference as a bar segment of negative width.
    """
    segment = _segment()
    claimer = _company(conn, "claims-legal.ch")
    proven_a = _company(conn, "did-legal-a.ch")
    proven_b = _company(conn, "did-legal-b.ch")

    # One company says it serves legal; two different companies have actually
    # delivered there. The union is three, and no arithmetic may say otherwise.
    conn.execute(
        "INSERT INTO tag (company_id, facet, value) VALUES (?, 'vertical', 'legal')",
        (claimer,),
    )
    for company_id in (proven_a, proven_b):
        conn.execute(
            """INSERT INTO case_study
                 (company_id, title, industry, summary, evidence_url, evidence_quote, extracted_at)
               VALUES (?, 'A project', 'legal', '', 'https://x/', 'q', '2026-01-01')""",
            (company_id,),
        )
    conn.commit()

    stats = analytics.collect(conn, segment)
    legal = next(row for row in stats.industries if row.industry == "legal")

    assert legal.providers == 3
    assert legal.with_evidence == 2
    for row in stats.industries:
        assert row.providers >= row.with_evidence, (
            f"{row.industry}: {row.providers} providers but {row.with_evidence} with evidence"
        )
