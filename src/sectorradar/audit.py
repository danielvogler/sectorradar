"""What this dataset is probably missing.

Every gap this tool has ever had was found by a person looking at the output
and saying "that can't be right": one company in Basel, ninety-nine without an
address, a hundred and forty-eight doing retrieval augmentation, no technology
recorded anywhere. Each was fixed. None of them would have been *noticed* by
anybody running the tool for the first time, which makes the fixes worth much
less than they look.

So this module does the noticing. It knows nothing about any particular market
— every check is a structural question that has a defensible threshold, and
every finding says what to do rather than only what is wrong.

It is deliberately not a pass/fail gate. A market genuinely can have one
company in a canton, and a tool that refuses to finish because of that is a
tool people learn to ignore. These are findings, ranked, with the reasoning
attached, printed at the end of every run.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Final, Literal

from sectorradar.config import Segment

Severity = Literal["high", "medium", "low"]

#: Resident population by canton, in thousands, from the federal statistics
#: office. Used only to ask whether discovery reached a region at all — a
#: canton with a twelfth of the country's population and a hundredth of its
#: companies is a discovery gap far more often than a real desert.
CANTON_POPULATION: Final[dict[str, int]] = {
    "ZH": 1579,
    "BE": 1051,
    "VD": 826,
    "AG": 704,
    "SG": 519,
    "GE": 514,
    "LU": 421,
    "TI": 353,
    "VS": 355,
    "BL": 292,
    "SO": 281,
    "FR": 331,
    "TG": 289,
    "GR": 202,
    "BS": 197,
    "NE": 176,
    "SZ": 165,
    "ZG": 130,
    "AR": 55,
    "SH": 84,
    "JU": 74,
    "AI": 16,
    "OW": 39,
    "NW": 44,
    "GL": 41,
    "UR": 37,
}

#: A canton is flagged when it holds this share of the population but less than
#: a third of that share of the companies. Deliberately generous: economic
#: activity is not evenly spread, and Zug holds far more companies per head
#: than Jura ever will.
_POPULATION_FLOOR: Final = 0.02
_UNDER_REPRESENTATION: Final = 3.0

#: A facet value applied to more than this share of companies has stopped
#: discriminating: it is either genuinely universal, in which case it is not
#: worth a column, or its evidence words match too loosely.
_SATURATED_TAG: Final = 0.75

#: Below this share of enriched companies carrying an attribute, the extraction
#: is probably not finding it rather than the market not having it.
_THIN_ATTRIBUTE: Final = 0.15

#: Share of a discovery run's results that were companies nobody had seen
#: before. While this stays high the market has more in it than has been found,
#: and stopping is a decision to have an incomplete answer.
_STILL_PRODUCTIVE: Final = 0.10
_EXHAUSTED: Final = 0.02


@dataclass
class Finding:
    """One thing worth looking at, and what to do about it."""

    severity: Severity
    area: str
    what: str
    do: str
    detail: str = ""

    def render(self) -> str:
        mark = {"high": "!!", "medium": " !", "low": "  "}[self.severity]
        lines = [f"{mark} [{self.area}] {self.what}"]
        if self.detail:
            lines.append(f"      {self.detail}")
        lines.append(f"      → {self.do}")
        return "\n".join(lines)


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: Severity, area: str, what: str, do: str, detail: str = "") -> None:
        self.findings.append(Finding(severity, area, what, do, detail))

    @property
    def ranked(self) -> list[Finding]:
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(self.findings, key=lambda f: (order[f.severity], f.area))

    def render(self) -> str:
        if not self.findings:
            return "no coverage gaps found."
        return "\n".join(f.render() for f in self.ranked)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def audit(conn: sqlite3.Connection, segment: Segment) -> AuditReport:
    """Look for the gaps somebody would otherwise have to spot by eye."""
    report = AuditReport()
    slug = segment.slug

    in_segment = _scalar(
        conn, "SELECT COUNT(*) FROM membership WHERE segment_slug = ? AND tier IS NOT NULL", (slug,)
    )
    if in_segment == 0:
        report.add(
            "high",
            "discovery",
            "no companies were classified into this segment",
            "Check the inclusion rule reads as a shortlist instruction, then re-run classify.",
        )
        return report

    _check_definition(segment, report)
    _check_geography(conn, segment, report, in_segment)
    _check_addresses(conn, segment, report)
    _check_crawl_depth(conn, segment, report)
    _check_vocabulary(conn, segment, report, in_segment)
    _check_attributes(conn, segment, report)
    _check_saturation(conn, segment, report)
    _check_gold_set(conn, segment, report)
    _check_enrichment(conn, segment, report)
    return report


def _check_definition(segment: Segment, report: AuditReport) -> None:
    """Whether the boundary was actually drawn, or only gestured at.

    Config validation catches an inclusion rule that instructs nothing. It
    cannot catch one that instructs loosely, and that is the more common
    failure: a rule with no exclusions admits everything adjacent, and the
    result looks like a large market rather than a badly-drawn one.
    """
    rule = segment.inclusion.casefold()
    if "exclude" not in rule:
        report.add(
            "medium",
            "definition",
            "the inclusion rule names nothing to exclude",
            "Add two or three `Exclude ...` sentences from real near-misses — firms "
            "that look like they belong and do not. They do more for precision than "
            "any amount of query tuning, and re-tiering is cheap: `classify` re-runs "
            "without re-crawling.",
        )

    tiers = segment.tiers
    if len(tiers) < 2:
        report.add(
            "low",
            "definition",
            f"only {len(tiers)} tier is defined, so everything admitted looks alike",
            "Tiers are the difference between 'this is what they are' and 'they touch "
            "it'. Two is usually enough to make a list worth sorting.",
        )


def _check_geography(
    conn: sqlite3.Connection, segment: Segment, report: AuditReport, total: int
) -> None:
    """Regions the search plausibly never reached.

    This is the check that would have caught "there is only one company in
    Basel" — a question a person asked, three weeks in, because they happened
    to know the city.
    """
    if segment.geo.country != "CH":
        return

    # A segment may restrict itself to particular cantons, and most small ones
    # do. Comparing a canton-scoped market against the whole country produced
    # fifteen findings on a correct single-canton run, each recommending
    # queries for a region the segment deliberately excludes — advice that
    # would damage the dataset if followed. A noisy audit is an unread audit,
    # and one that is confidently wrong is worse than silent.
    in_scope = set(segment.geo.cantons or CANTON_POPULATION)
    if len(in_scope) < 2:
        # One canton, or none named: there is no distribution to be uneven.
        return

    counts = {
        str(r["canton"]): int(r["n"])
        for r in conn.execute(
            """SELECT c.canton, COUNT(*) AS n FROM company c
                 JOIN membership m ON m.company_id = c.id
                WHERE m.segment_slug = ? AND m.tier IS NOT NULL AND c.canton IS NOT NULL
             GROUP BY c.canton""",
            (segment.slug,),
        ).fetchall()
    }
    placed = sum(counts.values()) or 1
    queries = " ".join(
        str(q).casefold() for q in (getattr(segment.source("websearch"), "queries", None) or [])
    )

    # Population shares are recomputed over the cantons the segment covers, so
    # a three-canton market is judged against those three.
    scoped = {c: p for c, p in CANTON_POPULATION.items() if c in in_scope}
    total_population = sum(scoped.values()) or 1

    thin: list[str] = []
    for canton, population in scoped.items():
        share_of_people = population / total_population
        if share_of_people < _POPULATION_FLOOR:
            continue
        share_of_companies = counts.get(canton, 0) / placed
        if share_of_companies * _UNDER_REPRESENTATION < share_of_people:
            thin.append(f"{canton} ({counts.get(canton, 0)})")

    if thin:
        report.add(
            "medium",
            "geography",
            f"{len(thin)} populous cantons hold far fewer companies than their size suggests",
            "Add city-level queries for them in segments/"
            f"{segment.slug}.yaml — national queries return national players.",
            ", ".join(sorted(thin)),
        )

    # A region nobody searched for is a region nobody found.
    unqueried = [
        canton
        # The larger cantons *this segment covers*. A hardcoded national list
        # named regions a canton-scoped market has no business reaching.
        for canton in sorted(scoped, key=lambda c: -scoped[c])[:8]
        if counts.get(canton, 0) <= 1 and canton.casefold() not in queries
    ]
    if unqueried:
        report.add(
            "low",
            "geography",
            "some cantons appear in neither the results nor the queries",
            "Consider naming their main cities in the query list.",
            ", ".join(unqueried),
        )


def _check_addresses(conn: sqlite3.Connection, segment: Segment, report: AuditReport) -> None:
    total = _scalar(
        conn,
        "SELECT COUNT(*) FROM membership WHERE segment_slug = ? AND tier IN (1, 2)",
        (segment.slug,),
    )
    if total == 0:
        return

    missing = _scalar(
        conn,
        """SELECT COUNT(*) FROM company c JOIN membership m ON m.company_id = c.id
            WHERE m.segment_slug = ? AND m.tier IN (1, 2) AND c.city IS NULL AND c.street IS NULL""",
        (segment.slug,),
    )
    if missing / total > 0.25:
        report.add(
            "medium",
            "address",
            f"{missing} of {total} tier 1-2 companies have no address at all",
            "Check the crawler reached an imprint or contact page for them; "
            "`fetch.CANDIDATE_PATHS` is where those paths are listed.",
        )

    never_tried = _scalar(
        conn,
        """SELECT COUNT(*) FROM company c JOIN membership m ON m.company_id = c.id
            WHERE m.segment_slug = ? AND m.tier IS NOT NULL AND c.lat IS NULL
              AND c.geocode_status IS NULL AND (c.city IS NOT NULL OR c.street IS NOT NULL)""",
        (segment.slug,),
    )
    if never_tried:
        report.add(
            "high",
            "address",
            f"{never_tried} companies have an address that was never geocoded",
            "Run `sectorradar geocode` — these are free to place and are simply missing.",
        )


def _check_crawl_depth(conn: sqlite3.Connection, segment: Segment, report: AuditReport) -> None:
    """Whether the pages that carry the interesting claims were reached at all."""
    enriched = _scalar(
        conn,
        """SELECT COUNT(DISTINCT p.company_id) FROM page p
             JOIN membership m ON m.company_id = p.company_id
            WHERE m.segment_slug = ? AND m.tier IS NOT NULL""",
        (segment.slug,),
    )
    if enriched == 0:
        report.add(
            "high",
            "crawl",
            "no pages were stored for any company in the segment",
            "Run `sectorradar fetch`. Nothing downstream can work without it.",
        )
        return

    # Match the words the crawler itself looks for, not a guess at them. A
    # single `LIKE '%referen%'` reported 12 of 141 companies with a reference
    # page while the database held 468 case studies — a check that cries wolf
    # about its own pipeline is worse than no check, because it teaches people
    # to skim past the whole report.
    from sectorradar import fetch

    for label, hints, do in (
        (
            "reference or case-study",
            fetch.REFERENCE_HINTS,
            "Reference pages carry the only evidence of delivered work. "
            "Check `fetch.REFERENCE_HINTS` covers how this market names them.",
        ),
        (
            "press or news",
            ("press", "presse", "news", "medien", "media", "aktuell"),
            "Coverage somebody else wrote is the one signal a company cannot assert. "
            "Add the paths this market uses to `fetch.CANDIDATE_PATHS`.",
        ),
    ):
        clause = " OR ".join("p.url LIKE ?" for _ in hints)
        reached = _scalar(
            conn,
            f"""SELECT COUNT(DISTINCT p.company_id) FROM page p
                  JOIN membership m ON m.company_id = p.company_id
                 WHERE m.segment_slug = ? AND m.tier IS NOT NULL AND ({clause})""",  # noqa: S608
            (segment.slug, *(f"%{h}%" for h in hints)),
        )
        if reached / enriched < 0.2:
            report.add(
                "medium",
                "crawl",
                f"only {reached} of {enriched} crawled companies had a {label} page reached",
                do,
            )


def _check_vocabulary(
    conn: sqlite3.Connection, segment: Segment, report: AuditReport, total: int
) -> None:
    """Whether the declared vocabulary matches the market that was found.

    Two failures, opposite in shape. A value nothing was tagged with is either
    absent from this market or its evidence words do not match the language the
    market writes in — and the second is far more common. A value almost
    everything carries has stopped telling you anything.
    """
    for facet in segment.facets:
        declared = segment.facet_values(facet)
        if not declared:
            continue
        used = {
            str(r["value"]): int(r["n"])
            for r in conn.execute(
                """SELECT t.value, COUNT(DISTINCT t.company_id) AS n FROM tag t
                     JOIN membership m ON m.company_id = t.company_id
                    WHERE m.segment_slug = ? AND m.tier IS NOT NULL AND t.facet = ?
                 GROUP BY t.value""",
                (segment.slug, facet),
            ).fetchall()
        }

        unused = [v for v in declared if used.get(v, 0) == 0]
        if unused and len(unused) > len(declared) * 0.3:
            report.add(
                "medium",
                "vocabulary",
                f"{len(unused)} of {len(declared)} `{facet}` values were never applied",
                "Usually the evidence words, not the market: a value only survives if its "
                "words appear on the page, in the language the site is written in. "
                f"See segments/AGENTS.md, and set `{facet}: {{value: [words]}}`.",
                ", ".join(sorted(unused)[:10]),
            )

        saturated = [v for v, n in used.items() if n / total > _SATURATED_TAG]
        if saturated:
            report.add(
                "low",
                "vocabulary",
                f"`{facet}` values applied to almost every company",
                "Either genuinely universal and not worth a column, or the evidence "
                "words match too loosely to discriminate.",
                ", ".join(sorted(saturated)),
            )


def _check_attributes(conn: sqlite3.Connection, segment: Segment, report: AuditReport) -> None:
    enriched = _scalar(
        conn,
        """SELECT COUNT(DISTINCT o.company_id) FROM offering o
             JOIN membership m ON m.company_id = o.company_id
            WHERE m.segment_slug = ? AND m.tier IS NOT NULL""",
        (segment.slug,),
    )
    if enriched == 0:
        report.add(
            "high",
            "extraction",
            "no company in the segment has a single extracted offering",
            "Run `sectorradar extract`, then check its hallucination rate — every claim "
            "is dropped unless its quote is found on the page it cites.",
        )
        return

    for field_name, human, do in (
        (
            "technologies",
            "named technology",
            "Check the extraction prompt asks for it and that "
            "reference or service pages were crawled.",
        ),
        (
            "hosting",
            "hosting model",
            "Often genuinely absent. Worth confirming against two "
            "sites by hand before treating it as a gap.",
        ),
    ):
        have = _scalar(
            conn,
            """SELECT COUNT(DISTINCT f.company_id) FROM company_field f
                 JOIN membership m ON m.company_id = f.company_id
                WHERE m.segment_slug = ? AND m.tier IS NOT NULL AND f.field = ?""",
            (segment.slug, field_name),
        )
        if have / enriched < _THIN_ATTRIBUTE:
            report.add(
                "low",
                "extraction",
                f"only {have} of {enriched} companies have a {human} recorded",
                do,
            )


def _check_saturation(conn: sqlite3.Connection, segment: Segment, report: AuditReport) -> None:
    """Whether discovery has stopped finding new companies, or was just stopped.

    The distinction nobody makes by eye. A run that returns a quarter of its
    results as companies never seen before has not exhausted the market — it
    has been switched off partway, and the answer is short by an unknown
    amount. That is a fine decision to take deliberately and a bad one to take
    by accident, so it is stated rather than left in a table.
    """
    rows = conn.execute(
        """SELECT source,
                  SUM(results_n) AS results,
                  SUM(new_unique_n) AS new_unique,
                  MAX(started_at) AS last_run
             FROM discovery_run
            WHERE segment_slug = ? AND source != 'seeds'
         GROUP BY source, SUBSTR(started_at, 1, 13)
         ORDER BY last_run DESC""",
        (segment.slug,),
    ).fetchall()
    if not rows:
        report.add(
            "high",
            "discovery",
            "no discovery has been run for this segment",
            "Run `sectorradar discover --segment " + segment.slug + "`. Everything else "
            "works from what it finds.",
        )
        return

    # The most recent pass per source is the only one that says anything about
    # now; earlier passes were productive by definition, since the dataset was
    # emptier then.
    latest: dict[str, tuple[int, int]] = {}
    for row in rows:
        source = str(row["source"])
        if source not in latest:
            latest[source] = (int(row["results"] or 0), int(row["new_unique"] or 0))

    productive = []
    exhausted = []
    for source, (results, new_unique) in latest.items():
        if results < 20:
            continue
        yield_rate = new_unique / results
        if yield_rate >= _STILL_PRODUCTIVE:
            productive.append(f"{source} ({new_unique} new of {results}, {yield_rate:.0%})")
        elif yield_rate <= _EXHAUSTED:
            exhausted.append(source)

    if productive:
        report.add(
            "high",
            "discovery",
            "the last discovery pass was still finding companies nobody had seen",
            "Run `sectorradar deepen` — it repeats discovery, writes fresh queries "
            "between rounds, and stops when a round turns up almost nothing new rather "
            "than when you do. The market is larger than what has been found so far.",
            ", ".join(productive),
        )

    if exhausted and not productive:
        report.add(
            "low",
            "discovery",
            f"{', '.join(exhausted)} returned almost nothing new on the last pass",
            "That channel is spent. More queries of the same kind will not help — "
            "a different source will. Directories, job ads and the company register "
            "reach firms that search does not.",
        )


def _check_gold_set(conn: sqlite3.Connection, segment: Segment, report: AuditReport) -> None:
    """The one measure of discovery that means anything."""
    gold = segment.gold_domains()
    if not gold:
        report.add(
            "high",
            "gold set",
            "this segment has no gold set, so discovery is unmeasured",
            "List a dozen companies you already know belong. Your own knowledge of "
            "the market beats every automated channel here, and it takes five minutes.",
        )
        return

    present = {
        str(r["domain"]).lower().removeprefix("www.")
        for r in conn.execute("SELECT domain FROM company").fetchall()
    }
    missed = sorted(gold - present)
    if missed:
        report.add(
            "high",
            "gold set",
            f"{len(missed)} known companies were not found at all",
            "Discovery is not reaching them. Add queries a buyer would use to find "
            "them, or seed them and treat recall as unmeasured until they arrive unaided.",
            ", ".join(missed[:8]),
        )

    excluded = [
        str(r["domain"])
        for r in conn.execute(
            """SELECT c.domain FROM company c JOIN membership m ON m.company_id = c.id
                WHERE m.segment_slug = ? AND m.tier IS NULL""",
            (segment.slug,),
        ).fetchall()
        if str(r["domain"]).lower().removeprefix("www.") in gold
    ]
    if excluded:
        report.add(
            "high",
            "gold set",
            f"{len(excluded)} known companies were found and then classified out",
            "Either the inclusion rule is too tight or their sites do not say what you "
            "know about them. Read the rationale on each before loosening anything.",
            ", ".join(sorted(excluded)[:8]),
        )


def _check_enrichment(conn: sqlite3.Connection, segment: Segment, report: AuditReport) -> None:
    tiered = _scalar(
        conn,
        "SELECT COUNT(*) FROM membership WHERE segment_slug = ? AND tier IN (1, 2)",
        (segment.slug,),
    )
    if tiered == 0:
        return

    bare = _scalar(
        conn,
        """SELECT COUNT(*) FROM membership m
            WHERE m.segment_slug = ? AND m.tier IN (1, 2)
              AND NOT EXISTS (SELECT 1 FROM case_study s WHERE s.company_id = m.company_id)
              AND NOT EXISTS (SELECT 1 FROM client_reference r WHERE r.company_id = m.company_id)
              AND NOT EXISTS (SELECT 1 FROM product p WHERE p.company_id = m.company_id)""",
        (segment.slug,),
    )
    if bare / tiered > 0.5:
        report.add(
            "low",
            "extraction",
            f"{bare} of {tiered} tier 1-2 companies show no delivered work at all",
            "Often true — many firms publish nothing. Confirm on two sites by hand: if they "
            "do have reference pages, the crawler is not reaching them.",
        )
