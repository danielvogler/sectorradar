"""Keep searching until it stops finding new companies, not until somebody stops asking.

A single discovery pass runs the queries in the segment file and stops. Whether
that was enough is a judgement, and it was being left to whoever happened to be
watching — a person reading a yield number, or an agent deciding it had done
enough. Neither is reliable. Agents differ in how long they persist, people get
bored, and the failure is invisible: a half-searched market produces a dataset
that looks complete.

So the loop lives here. It runs a pass, measures how much of what came back was
new, and if the market is still giving it writes more queries and goes again.
It stops on saturation, on a round cap, or on a spend cap, whichever comes
first — and says which.

**Every ceiling is set before the first round, never after the surprise.** An
autonomous loop with no bound is a bill with no bound.

The queries it writes are the interesting part. Expansion is not paraphrase: a
model asked for "more queries like these" returns synonyms that find the same
companies again. It is told what has already been found — which cities, which
kinds of firm — and asked for queries that would surface companies *unlike*
those, in the languages the market sells in. Coverage comes from angles, not
from volume.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from sectorradar import discover as discover_mod
from sectorradar.config import Segment, Settings
from sectorradar.llm import LLMClient
from sectorradar.logging import get_logger
from sectorradar.models import Frozen

log = get_logger(__name__)

#: Below this share of results being new, a pass has stopped paying for itself.
SATURATED_AT: Final = 0.08

#: Ceilings. Set before the first round, deliberately low enough that an
#: unattended loop cannot surprise anybody.
DEFAULT_MAX_ROUNDS: Final = 6
DEFAULT_MAX_SPEND_USD: Final = 5.0

#: New queries per round. Enough to open a few angles, few enough that a bad
#: expansion is cheap to notice and discard.
QUERIES_PER_ROUND: Final = 8


class QuerySet(Frozen):
    """Queries a model proposes for the next round."""

    queries: tuple[str, ...] = ()


@dataclass
class Round:
    number: int
    found: int = 0
    new: int = 0
    queries_added: int = 0

    @property
    def yield_rate(self) -> float:
        return self.new / self.found if self.found else 0.0


@dataclass
class DeepenReport:
    rounds: list[Round] = field(default_factory=list)
    stopped_because: str = ""
    total_new: int = 0
    spend_usd: float = 0.0
    #: Queries this run invented. Persisted, so a later run continues from
    #: them rather than starting over — and printed, because whether one earned
    #: a place in the segment file is a judgement for whoever owns it.
    queries_invented: list[str] = field(default_factory=list)
    #: Queries carried over from earlier runs.
    recalled: int = 0

    def render(self) -> str:
        lines = [f"{'round':>6}  {'found':>7}  {'new':>6}  {'yield':>6}  queries added"]
        for r in self.rounds:
            lines.append(
                f"{r.number:>6}  {r.found:>7}  {r.new:>6}  {r.yield_rate:>5.0%}  {r.queries_added}"
            )
        lines.append("")
        if self.recalled:
            lines.append(f"reused {self.recalled} queries invented by earlier runs")
        lines.append(f"stopped: {self.stopped_because}")
        lines.append(f"new companies this run: {self.total_new}")
        lines.append(f"spend: USD {self.spend_usd:.4f}")
        if self.queries_invented:
            lines.append("")
            lines.append("queries worth keeping — add the productive ones to the segment file:")
            lines += [f"  - {q!r}" for q in self.queries_invented]
        return "\n".join(lines)


def remember(conn: sqlite3.Connection, slug: str, queries: Iterable[str]) -> None:
    """Record queries this run invented, so a later run starts from them."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for query in queries:
        conn.execute(
            """INSERT INTO learned_query (segment_slug, query, invented_at)
               VALUES (?, ?, ?)
               ON CONFLICT(segment_slug, query) DO NOTHING""",
            (slug, query, now),
        )
    conn.commit()


def learned(conn: sqlite3.Connection, slug: str) -> list[str]:
    """Queries earlier runs invented, newest first.

    Without these a second run re-runs the segment file's own queries, finds
    nothing new because the first run already took them, and reports
    "saturated" — which is true of those queries and says nothing about the
    market. A search that cannot remember what it tried is the same shallow
    search repeated.
    """
    return [
        str(r["query"])
        for r in conn.execute(
            "SELECT query FROM learned_query WHERE segment_slug = ? ORDER BY id DESC",
            (slug,),
        ).fetchall()
    ]


def _found_so_far(conn: sqlite3.Connection, slug: str) -> tuple[list[str], list[str]]:
    """Cities and company names already discovered, to steer away from them."""
    cities = [
        str(r["city"])
        for r in conn.execute(
            """SELECT c.city, COUNT(*) AS n FROM company c
                 JOIN membership m ON m.company_id = c.id
                WHERE m.segment_slug = ? AND c.city IS NOT NULL
             GROUP BY c.city ORDER BY n DESC LIMIT 12""",
            (slug,),
        ).fetchall()
    ]
    names = [
        str(r["canonical_name"])
        for r in conn.execute(
            """SELECT c.canonical_name FROM company c
                 JOIN membership m ON m.company_id = c.id
                WHERE m.segment_slug = ? AND m.tier IS NOT NULL
             ORDER BY RANDOM() LIMIT 25""",
            (slug,),
        ).fetchall()
    ]
    return cities, names


def build_expansion_prompt(
    segment: Segment, tried: list[str], cities: list[str], names: list[str], want: int
) -> str:
    """Ask for angles that have not been tried, not for synonyms of ones that have."""
    return f"""You are widening a search for companies in one specific market.

## The market

{segment.inclusion.strip()}

Country: {segment.geo.country}

## Queries already run ({len(tried)})

{chr(10).join("- " + q for q in tried[:60])}

## What those queries already found

Companies (a sample): {", ".join(names) if names else "none yet"}
Concentrated in: {", ".join(cities) if cities else "no locations recorded yet"}

## What to produce

{want} new search queries that would surface companies in this market that the
queries above would **miss**.

Do not paraphrase the queries already run. A synonym returns the same companies
and wastes a round. Find different angles instead:

- Regions and cities that are absent from the list above
- Languages the market sells in that are under-represented in the queries
- The vocabulary a *buyer* would use, which is often not the vocabulary a
  supplier uses to describe itself
- Adjacent job titles, certifications, tools or regulations that only firms in
  this market would mention
- Channels rather than descriptions: partner directories, association
  membership, conference speaker lists, job advertisements

Each query must be something you would type into a search engine. No
explanations, no boolean operators, no site: filters."""


def deepen(
    conn: sqlite3.Connection,
    segment: Segment,
    settings: Settings,
    client: LLMClient,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_spend_usd: float = DEFAULT_MAX_SPEND_USD,
    saturated_at: float = SATURATED_AT,
    dry_run: bool = False,
) -> DeepenReport:
    """Discover repeatedly, widening the queries, until a round finds almost nothing new."""
    report = DeepenReport()
    base = [str(q) for q in (getattr(segment.source("websearch"), "queries", None) or [])]
    remembered = learned(conn, segment.slug)

    # Everything already tried, so expansion is asked for angles neither the
    # segment file nor any earlier run has covered.
    tried: list[str] = base + [q for q in remembered if q not in base]
    report.recalled = len(remembered)

    # Round one runs what earlier runs invented as well as the segment's own
    # queries. Skipping them is what made a second `deepen` conclude a market
    # was exhausted when it had only forgotten where it got to.
    working = segment
    if remembered:
        first = segment.model_copy(deep=True)
        first.sources["websearch"].queries = tried  # type: ignore[attr-defined]
        working = first

    for number in range(1, max_rounds + 1):
        result = discover_mod.discover(
            conn, working, settings, sources=["websearch"], dry_run=dry_run
        )
        websearch = next((r for r in result.results if r.source == "websearch"), None)
        current = Round(
            number=number,
            found=websearch.found if websearch else 0,
            new=websearch.new_unique if websearch else 0,
        )
        report.rounds.append(current)
        report.total_new += current.new

        log.info(
            "deepen.round",
            round=number,
            found=current.found,
            new=current.new,
            yield_rate=round(current.yield_rate, 3),
        )

        if current.found and current.yield_rate < saturated_at:
            report.stopped_because = (
                f"saturated — round {number} returned {current.yield_rate:.0%} new, "
                f"below the {saturated_at:.0%} floor"
            )
            return report
        if number >= max_rounds:
            report.stopped_because = (
                f"hit the round cap ({max_rounds}) while still finding "
                f"{current.yield_rate:.0%} new — the market has more in it"
            )
            return report
        if report.spend_usd >= max_spend_usd:
            report.stopped_because = f"hit the spend cap (USD {max_spend_usd:.2f})"
            return report

        # Still productive, so widen rather than repeat.
        cities, names = _found_so_far(conn, segment.slug)
        proposal = client.structured(
            build_expansion_prompt(working, tried, cities, names, QUERIES_PER_ROUND),
            QuerySet,
            temperature=0.7,  # variety is the point here, unlike everywhere else
        )
        report.spend_usd += proposal.usage.cost_usd

        fresh = [
            q.strip()
            for q in (proposal.value.queries if proposal.value else ())
            if q.strip() and q.strip().casefold() not in {t.casefold() for t in tried}
        ]
        if not fresh:
            report.stopped_because = "no new angles could be found to try"
            return report

        tried += fresh
        report.queries_invented += fresh
        current.queries_added = len(fresh)
        if not dry_run:
            remember(conn, segment.slug, fresh)

        # A copy carrying the widened query list. The segment file on disk is
        # never rewritten: which queries earned their keep is a judgement for
        # whoever owns that file, and the productive ones are printed at the end.
        widened = working.model_copy(deep=True)
        widened.sources["websearch"].queries = fresh  # type: ignore[attr-defined]
        working = widened

    report.stopped_because = f"hit the round cap ({max_rounds})"
    return report
