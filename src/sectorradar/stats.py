"""Coverage, saturation and cost.

Gold-set recall is the single most important number in this project and the
first thing that gets skipped. Without it there is no way to tell whether a
prompt change helped, and prompt tuning proceeds on vibes for a fortnight.

The gold set is a hand-written list of firms the owner already knows belong in
the segment, stored in the segment YAML. Recall is the share of them the
pipeline found on its own. It says nothing about precision — a run that
returned every company in Switzerland would score 100% — so it is read
alongside the tier counts, never on its own.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from sectorradar.config import Segment
from sectorradar.logging import get_logger
from sectorradar.resolve import normalise_domain

log = get_logger(__name__)


@dataclass(frozen=True)
class Recall:
    """How much of the known-good set the pipeline found by itself.

    Two figures, because the headline one is easy to fool.

    ``percent`` is recall over the whole gold set, which is what the build
    specification asks for. It has a serious weakness: any gold entry that is
    also in ``seeds.urls`` is in the database *by definition*, because seeding
    put it there. A gold set assembled from the seed list therefore scores near
    100% no matter how bad discovery is.

    ``blind_percent`` is recall over gold entries that were never seeded. It is
    the number worth tuning prompts and queries against, and the one to quote
    when someone asks how good coverage is.
    """

    expected: int
    found: int
    missing: tuple[str, ...] = ()
    blind_expected: int = 0
    blind_found: int = 0
    #: Gold entries an automated source reached without being handed the domain.
    unseeded_sources_found: int = 0

    @property
    def ratio(self) -> float:
        return self.found / self.expected if self.expected else 0.0

    @property
    def percent(self) -> float:
        return round(self.ratio * 100, 1)

    @property
    def blind_percent(self) -> float:
        if not self.blind_expected:
            return 0.0
        return round(self.blind_found / self.blind_expected * 100, 1)

    @property
    def is_mostly_seeded(self) -> bool:
        """Whether the headline figure is largely measuring the seed list."""
        if not self.expected:
            return False
        return (self.expected - self.blind_expected) / self.expected > 0.5


@dataclass(frozen=True)
class SourceStats:
    source: str
    queries: int
    results: int
    new_unique: int
    cost_usd: float

    @property
    def yield_ratio(self) -> float:
        """New-unique per result. The number that says whether to keep querying."""
        return self.new_unique / self.results if self.results else 0.0


@dataclass
class Stats:
    segment: str
    companies: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_review: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    rejected_candidates: int = 0
    geocoded: int = 0
    offerings: int = 0
    with_rationale: int = 0
    recall: Recall = field(default_factory=lambda: Recall(0, 0))
    sources: list[SourceStats] = field(default_factory=list)
    total_cost_usd: float = 0.0


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def gold_set_recall(conn: sqlite3.Connection, segment: Segment) -> Recall:
    """Share of the gold set present in the database.

    Domains are compared after the same normalisation resolution uses, so a
    gold entry written as ``https://www.Example.ch/`` matches a stored
    ``example.ch``. Comparing raw strings would understate recall for no reason
    other than punctuation.
    """
    expected = {
        normalise_domain(domain) or domain.lower().removeprefix("www.")
        for domain in segment.gold_domains()
    }
    if not expected:
        return Recall(expected=0, found=0)

    stored = {
        str(row["domain"]).lower()
        for row in conn.execute(
            """
            SELECT c.domain FROM company c
              JOIN membership m ON m.company_id = c.id
             WHERE m.segment_slug = ?
            """,
            (segment.slug,),
        ).fetchall()
    }

    found = expected & stored

    # Gold entries that seeding did not simply hand to the pipeline.
    seeded: set[str] = set()
    for entry in getattr(segment.source("seeds"), "urls", None) or []:
        raw = entry.get("url") if isinstance(entry, dict) else entry
        domain = normalise_domain(str(raw)) if raw else None
        if domain:
            seeded.add(domain)
    blind = expected - seeded

    return Recall(
        expected=len(expected),
        found=len(found),
        missing=tuple(sorted(expected - stored)),
        blind_expected=len(blind),
        blind_found=len(blind & stored),
        unseeded_sources_found=len(_reached_without_seeding(conn, segment, expected)),
    )


def _reached_without_seeding(
    conn: sqlite3.Connection, segment: Segment, expected: set[str]
) -> set[str]:
    """Gold domains some source other than ``seeds`` produced a candidate for."""
    rows = conn.execute(
        """
        SELECT DISTINCT c.domain
          FROM company c
          JOIN membership m ON m.company_id = c.id
          JOIN candidate cd ON cd.resolved_to = c.id
         WHERE m.segment_slug = ? AND cd.source != 'seeds'
        """,
        (segment.slug,),
    ).fetchall()
    return {str(r["domain"]).lower() for r in rows} & expected


def collect(conn: sqlite3.Connection, segment: Segment) -> Stats:
    """Everything `sectorradar stats` reports."""
    stats = Stats(segment=segment.slug)

    stats.companies = _scalar(
        conn, "SELECT COUNT(*) FROM membership WHERE segment_slug = ?", (segment.slug,)
    )
    stats.candidates = _scalar(
        conn, "SELECT COUNT(*) FROM candidate WHERE segment_slug = ?", (segment.slug,)
    )
    stats.rejected_candidates = _scalar(
        conn,
        "SELECT COUNT(*) FROM candidate WHERE segment_slug = ? AND reject_reason IS NOT NULL",
        (segment.slug,),
    )
    stats.geocoded = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM company c JOIN membership m ON m.company_id = c.id
         WHERE m.segment_slug = ? AND c.lat IS NOT NULL
        """,
        (segment.slug,),
    )
    stats.offerings = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM offering o JOIN membership m ON m.company_id = o.company_id
         WHERE m.segment_slug = ?
        """,
        (segment.slug,),
    )
    stats.with_rationale = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM membership
         WHERE segment_slug = ? AND tier IN (1, 2)
           AND tier_rationale IS NOT NULL AND tier_rationale != ''
        """,
        (segment.slug,),
    )

    for row in conn.execute(
        """
        SELECT COALESCE(CAST(tier AS TEXT), 'unclassified') AS tier, COUNT(*) AS n
          FROM membership WHERE segment_slug = ? GROUP BY tier ORDER BY tier
        """,
        (segment.slug,),
    ):
        stats.by_tier[str(row["tier"])] = int(row["n"])

    for row in conn.execute(
        """
        SELECT COALESCE(review_state, 'pending') AS state, COUNT(*) AS n
          FROM membership WHERE segment_slug = ? GROUP BY state ORDER BY state
        """,
        (segment.slug,),
    ):
        stats.by_review[str(row["state"])] = int(row["n"])

    for row in conn.execute(
        """
        SELECT source,
               COUNT(*)               AS queries,
               SUM(results_n)         AS results,
               SUM(new_unique_n)      AS new_unique,
               SUM(COALESCE(cost_usd, 0)) AS cost
          FROM discovery_run WHERE segment_slug = ? GROUP BY source ORDER BY source
        """,
        (segment.slug,),
    ):
        stats.sources.append(
            SourceStats(
                source=str(row["source"]),
                queries=int(row["queries"] or 0),
                results=int(row["results"] or 0),
                new_unique=int(row["new_unique"] or 0),
                cost_usd=float(row["cost"] or 0.0),
            )
        )

    stats.total_cost_usd = sum(s.cost_usd for s in stats.sources)
    stats.recall = gold_set_recall(conn, segment)
    return stats


def format_report(stats: Stats) -> str:
    """A plain-text report for the CLI."""
    lines = [
        f"segment            {stats.segment}",
        f"companies          {stats.companies}",
        f"  by tier          {stats.by_tier or '—'}",
        f"  by review        {stats.by_review or '—'}",
        f"  tier 1-2 with a written rationale  {stats.with_rationale}",
        f"candidates         {stats.candidates} ({stats.rejected_candidates} rejected)",
        f"geocoded           {stats.geocoded}",
        f"offerings          {stats.offerings}",
        "",
    ]

    recall = stats.recall
    if recall.expected:
        lines.append(f"gold-set recall    {recall.percent}% ({recall.found}/{recall.expected})")
        lines.append(
            f"  blind recall     {recall.blind_percent}% "
            f"({recall.blind_found}/{recall.blind_expected}) — gold entries never seeded"
        )
        lines.append(
            f"  reached unaided  {recall.unseeded_sources_found}/{recall.expected} "
            "— gold entries an automated source found without being handed the domain"
        )
        if recall.is_mostly_seeded:
            lines.append("  NOTE: most of the gold set is also in seeds.urls, so the headline")
            lines.append("        figure largely measures the seed list rather than discovery.")
            lines.append("        Read 'reached unaided' instead.")
        if recall.missing:
            lines.append(f"  not found        {', '.join(recall.missing)}")
    else:
        lines.append("gold-set recall    no gold set defined — recall cannot be measured")

    if stats.sources:
        lines.extend(["", "saturation by source", "  source          queries  results  new  yield"])
        for source in stats.sources:
            lines.append(
                f"  {source.source:<14} {source.queries:>7} {source.results:>8} "
                f"{source.new_unique:>4} {source.yield_ratio:>6.0%}"
            )

    lines.extend(["", f"discovery cost     USD {stats.total_cost_usd:.4f}"])
    return "\n".join(lines)
