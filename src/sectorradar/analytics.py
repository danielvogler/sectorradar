"""Aggregates over a segment.

Computed once, here, so that every view reads the same numbers. Two front ends
each deriving "how many companies serve finance" from their own SQL is two
chances to derive it differently, and the disagreement surfaces as a bug report
about the wrong thing.

Everything is a plain dataclass over query results, so it serialises to JSON
without ceremony and can be tested against a fixture database.

One rule runs through the whole module: **the operator's own companies are in
the dataset but never in a baseline they are compared against.** An average
that quietly includes you tells you that you are average.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from sectorradar import swiss
from sectorradar.config import Segment
from sectorradar.industries import canonical_industry
from sectorradar.logging import get_logger

log = get_logger(__name__)

#: Headcount bands. Coarse on purpose: the estimates are extracted from prose
#: and are not precise enough to justify finer buckets.
SIZE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("1-9", 1, 9),
    ("10-49", 10, 49),
    ("50-249", 50, 249),
    ("250+", 250, 10_000_000),
)


def band_for(headcount: int | None) -> str:
    if headcount is None:
        return "unknown"
    for label, low, high in SIZE_BANDS:
        if low <= headcount <= high:
            return label
    return "unknown"


@dataclass(frozen=True)
class Count:
    label: str
    n: int
    share: float = 0.0


@dataclass(frozen=True)
class IndustryCoverage:
    """How many companies serve an industry, and how deeply.

    ``providers`` counts companies claiming the sector at all; ``with_evidence``
    counts those with a named client or a case study in it. The gap between the
    two is the difference between a sector on a list and a sector somebody has
    actually worked in.
    """

    industry: str
    providers: int
    with_evidence: int
    share: float = 0.0


@dataclass(frozen=True)
class SignalByBand:
    signal: str
    #: Band label to the share of companies in that band whose site has it.
    by_band: dict[str, float]
    overall: float


@dataclass
class SearchBenchmark:
    """What the most findable sites do that the least findable ones do not.

    Split into quarters by search score rather than reported as one average,
    because the interesting fact is not the mean — it is which practices are
    near-universal at the top and rare at the bottom. That difference is the
    only thing here that reads as advice.
    """

    band: str
    companies: int
    avg_score: float
    structured_data: float
    faq_schema: float
    local_business: float
    hreflang: float
    open_graph: float
    median_words: int


@dataclass
class SegmentAnalytics:
    segment: str
    companies: int = 0
    compared: int = 0
    own: int = 0
    by_tier: list[Count] = field(default_factory=list)
    by_canton: list[Count] = field(default_factory=list)
    by_size: list[Count] = field(default_factory=list)
    services: list[Count] = field(default_factory=list)
    technologies: list[Count] = field(default_factory=list)
    industries: list[IndustryCoverage] = field(default_factory=list)
    signals: list[SignalByBand] = field(default_factory=list)
    search: list[SearchBenchmark] = field(default_factory=list)
    stack: dict[str, list[Count]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stack(
    conn: sqlite3.Connection,
    segment: Segment,
    exclude: str,
    params: tuple[Any, ...],
    total: int,
) -> dict[str, list[Count]]:
    """What the market builds with, and where it runs.

    These were extracted for weeks and stored nowhere, so they existed only
    inside a dialog nobody had a reason to open. Aggregated here they answer
    the question directly: which frameworks this market has standardised on,
    which clouds it will deploy to, and how many firms will put a system on a
    client's own hardware.
    """
    fields = ("technologies", "cloud_providers", "hosting", "certifications", "workshop_formats")
    result: dict[str, list[Count]] = {}

    # Which languages a site is published in. It was briefly deleted as a
    # "site signal", which was the wrong correction to the right complaint:
    # sitting in a list about how firms sell, it said nothing, because it is
    # not a fact about the offering. It is a fact about reach, and in a country
    # that buys in four languages that is worth its own row.
    language_rows = conn.execute(
        f"""SELECT c.languages FROM company c JOIN membership m ON m.company_id = c.id
             WHERE m.segment_slug = ? {exclude}
               AND c.languages IS NOT NULL AND c.languages != ''""",  # noqa: S608
        params,
    ).fetchall()
    counter: dict[str, int] = {}
    for row in language_rows:
        for code in str(row["languages"]).split(","):
            if code.strip():
                counter[code.strip()] = counter.get(code.strip(), 0) + 1
    if counter:
        order = {code: i for i, code in enumerate(swiss.LANGUAGES)}
        result["languages"] = [
            Count(label=code, n=n, share=_share(n, total))
            for code, n in sorted(counter.items(), key=lambda kv: order.get(kv[0], 99))
        ]

    for name in fields:
        rows = conn.execute(
            f"""SELECT f.value AS label, COUNT(DISTINCT f.company_id) AS n
                  FROM company_field f JOIN company c ON c.id = f.company_id
                  JOIN membership m ON m.company_id = c.id
                 WHERE m.segment_slug = ? {exclude} AND f.field = ?
              GROUP BY f.value ORDER BY n DESC, f.value""",  # noqa: S608
            (*params, name),
        ).fetchall()
        if rows:
            result[name] = _counts(rows, total)
    return result


def _search_benchmarks(conn: sqlite3.Connection, segment: Segment) -> list[SearchBenchmark]:
    """Quartiles of search score, with the practices that separate them.

    Own companies are excluded, like every other baseline here: comparing
    yourself against a field you are inside tells you that you are average.
    """
    own = _own_ids(conn, segment)
    rows = conn.execute(
        """
        SELECT s.company_id, s.score, s.schema_types, s.has_hreflang,
               s.has_open_graph, s.median_word_count
          FROM seo_profile s JOIN membership m ON m.company_id = s.company_id
         WHERE m.segment_slug = ? AND s.pages_analysed > 0
        ORDER BY s.score
        """,
        (segment.slug,),
    ).fetchall()
    rows = [r for r in rows if int(r["company_id"]) not in own]
    if len(rows) < 8:
        return []

    labels = ("bottom quarter", "lower middle", "upper middle", "top quarter")
    size = len(rows) // 4
    benchmarks: list[SearchBenchmark] = []
    for index, label in enumerate(labels):
        start = index * size
        end = len(rows) if index == 3 else start + size
        chunk = rows[start:end]
        if not chunk:
            continue
        n = len(chunk)
        schemas = [str(r["schema_types"] or "[]") for r in chunk]
        benchmarks.append(
            SearchBenchmark(
                band=label,
                companies=n,
                avg_score=round(sum(int(r["score"]) for r in chunk) / n, 1),
                structured_data=_share(sum(1 for s in schemas if s != "[]"), n),
                faq_schema=_share(sum(1 for s in schemas if "FAQPage" in s), n),
                local_business=_share(sum(1 for s in schemas if "LocalBusiness" in s), n),
                hreflang=_share(sum(1 for r in chunk if r["has_hreflang"]), n),
                open_graph=_share(sum(1 for r in chunk if r["has_open_graph"]), n),
                median_words=int(
                    statistics.median([int(r["median_word_count"] or 0) for r in chunk])
                ),
            )
        )
    return list(reversed(benchmarks))


def _share(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def _counts(rows: list[sqlite3.Row], total: int) -> list[Count]:
    return [
        Count(label=str(r["label"]), n=int(r["n"]), share=_share(int(r["n"]), total)) for r in rows
    ]


def _own_ids(conn: sqlite3.Connection, segment: Segment) -> set[int]:
    owned = segment.owned()
    if not owned:
        return set()
    placeholders = ",".join("?" * len(owned))
    sql = f"SELECT id FROM company WHERE domain IN ({placeholders})"  # noqa: S608
    return {int(r["id"]) for r in conn.execute(sql, tuple(owned)).fetchall()}


def collect(conn: sqlite3.Connection, segment: Segment) -> SegmentAnalytics:
    """Every aggregate the views need, for one segment."""
    own = _own_ids(conn, segment)

    # Two exclusions, for different reasons, and both matter.
    #
    # Own companies are excluded because a comparison you are inside tells you
    # that you are average. Companies with no tier are excluded because the
    # classifier already decided they are not in this market — they are
    # rejected candidates, and counting them made every aggregate on the page
    # wrong in the same direction: 99 companies of "unknown canton" in a
    # canton breakdown, when the segment itself had 25.
    exclude = "AND m.tier IS NOT NULL"
    params: tuple[Any, ...] = (segment.slug,)
    if own:
        exclude += f" AND c.id NOT IN ({','.join('?' * len(own))})"
        params = (segment.slug, *sorted(own))

    stats = SegmentAnalytics(segment=segment.slug)
    stats.own = int(
        conn.execute(
            f"SELECT COUNT(*) AS n FROM company c JOIN membership m ON m.company_id = c.id "  # noqa: S608
            f"WHERE m.segment_slug = ? AND m.tier IS NOT NULL"
            f"{' AND c.id IN (' + ','.join('?' * len(own)) + ')' if own else ' AND 0'}",
            (segment.slug, *sorted(own)) if own else (segment.slug,),
        ).fetchone()["n"]
    )
    stats.companies = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM membership WHERE segment_slug = ? AND tier IS NOT NULL",
            (segment.slug,),
        ).fetchone()["n"]
    )
    stats.compared = stats.companies - stats.own
    total = stats.compared or 1

    base = f"""
        FROM company c JOIN membership m ON m.company_id = c.id
       WHERE m.segment_slug = ? {exclude}
    """

    stats.by_tier = _counts(
        conn.execute(
            f"""SELECT COALESCE(CAST(m.tier AS TEXT), 'unclassified') AS label,
                       COUNT(*) AS n {base} GROUP BY label ORDER BY label""",
            params,
        ).fetchall(),
        total,
    )
    stats.by_canton = _counts(
        conn.execute(
            f"""SELECT COALESCE(NULLIF(c.canton, ''), 'unknown') AS label,
                       COUNT(*) AS n {base} GROUP BY label ORDER BY n DESC""",
            params,
        ).fetchall(),
        total,
    )

    # Size bands are computed here rather than in SQL, so the banding lives in
    # one place and the view cannot disagree with the export about it.
    sizes: dict[str, int] = {}
    for row in conn.execute(f"SELECT c.headcount_est AS h {base}", params).fetchall():
        label = band_for(int(row["h"]) if row["h"] is not None else None)
        sizes[label] = sizes.get(label, 0) + 1
    order = [b[0] for b in SIZE_BANDS] + ["unknown"]
    stats.by_size = [
        Count(label=b, n=sizes[b], share=_share(sizes[b], total)) for b in order if b in sizes
    ]

    stats.services = _counts(
        conn.execute(
            f"""SELECT t.value AS label, COUNT(DISTINCT t.company_id) AS n
                  FROM tag t JOIN company c ON c.id = t.company_id
                  JOIN membership m ON m.company_id = c.id
                 WHERE m.segment_slug = ? {exclude} AND t.facet = 'service_type'
              GROUP BY t.value ORDER BY n DESC""",  # noqa: S608
            params,
        ).fetchall(),
        total,
    )
    stats.technologies = _counts(
        conn.execute(
            f"""SELECT t.value AS label, COUNT(DISTINCT t.company_id) AS n
                  FROM tag t JOIN company c ON c.id = t.company_id
                  JOIN membership m ON m.company_id = c.id
                 WHERE m.segment_slug = ? {exclude} AND t.facet = 'tech'
              GROUP BY t.value ORDER BY n DESC""",  # noqa: S608
            params,
        ).fetchall(),
        total,
    )

    stats.industries = _industry_coverage(conn, segment, exclude, params, total)
    stats.signals = _signals_by_band(conn, segment, exclude, params)
    stats.search = _search_benchmarks(conn, segment)
    stats.stack = _stack(conn, segment, exclude, params, total)

    stats.totals = {
        row[0]: int(
            conn.execute(
                f"""SELECT COUNT(*) AS n FROM {row[1]} x
                     JOIN company c ON c.id = x.company_id
                     JOIN membership m ON m.company_id = c.id
                    WHERE m.segment_slug = ? {exclude}""",  # noqa: S608
                params,
            ).fetchone()["n"]
        )
        for row in (
            ("offerings", "offering"),
            ("case_studies", "case_study"),
            ("clients", "client_reference"),
            ("products", "product"),
            ("mentions", "media_mention"),
        )
    }
    return stats


def _industry_coverage(
    conn: sqlite3.Connection,
    segment: Segment,
    exclude: str,
    params: tuple[Any, ...],
    total: int,
) -> list[IndustryCoverage]:
    """Which sectors the market serves, and how thinly.

    A sector claimed on a page and a sector with a named client in it are
    different claims. Both are counted, because the gap between them is the
    interesting part.
    """
    # Companies that claim the sector on their site, via a `vertical` tag.
    claimed: dict[str, set[int]] = {}
    for row in conn.execute(
        f"""SELECT t.value AS industry, t.company_id
              FROM tag t JOIN company c ON c.id = t.company_id
              JOIN membership m ON m.company_id = c.id
             WHERE m.segment_slug = ? {exclude} AND t.facet = 'vertical'""",  # noqa: S608
        params,
    ).fetchall():
        industry = canonical_industry(str(row["industry"]))
        if industry:
            claimed.setdefault(industry, set()).add(int(row["company_id"]))

    # Companies with a named client or a written case study in the sector.
    evidenced: dict[str, set[int]] = {}
    for row in conn.execute(
        f"""SELECT industry, company_id FROM (
                SELECT cs.industry AS industry, cs.company_id AS company_id
                  FROM case_study cs JOIN company c ON c.id = cs.company_id
                  JOIN membership m ON m.company_id = c.id
                 WHERE m.segment_slug = ? {exclude} AND cs.industry IS NOT NULL
                 UNION
                SELECT cr.industry, cr.company_id
                  FROM client_reference cr JOIN company c ON c.id = cr.company_id
                  JOIN membership m ON m.company_id = c.id
                 WHERE m.segment_slug = ? {exclude} AND cr.industry IS NOT NULL
            )""",  # noqa: S608
        params + params,
    ).fetchall():
        industry = canonical_industry(str(row["industry"]))
        if industry:
            evidenced.setdefault(industry, set()).add(int(row["company_id"]))

    # `providers` is the union, not the tag count. Taking it from tags alone
    # produced `legal: 5 providers, 7 evidenced` — more companies with proof
    # than companies in the sector at all — which drew a bar segment of
    # negative width and split the chart in half.
    return sorted(
        (
            IndustryCoverage(
                industry=industry,
                providers=len(claimed.get(industry, set()) | evidenced.get(industry, set())),
                with_evidence=len(evidenced.get(industry, set())),
                share=_share(
                    len(claimed.get(industry, set()) | evidenced.get(industry, set())), total
                ),
            )
            for industry in claimed.keys() | evidenced.keys()
        ),
        key=lambda c: c.providers,
        reverse=True,
    )


def _signals_by_band(
    conn: sqlite3.Connection, segment: Segment, exclude: str, params: tuple[Any, ...]
) -> list[SignalByBand]:
    """What a website has, by company size.

    The comparison that answers "what do the bigger firms publish that we do
    not" — which is a question about presentation, not about capability.
    """
    rows = conn.execute(
        f"""SELECT s.signal AS signal, s.present AS present, c.headcount_est AS h
              FROM site_signal s JOIN company c ON c.id = s.company_id
              JOIN membership m ON m.company_id = c.id
             WHERE m.segment_slug = ? {exclude}""",  # noqa: S608
        params,
    ).fetchall()

    tally: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        band = band_for(int(row["h"]) if row["h"] is not None else None)
        per_signal = tally.setdefault(str(row["signal"]), {})
        per_signal.setdefault(band, []).append(int(row["present"]))

    out: list[SignalByBand] = []
    for signal, bands in sorted(tally.items()):
        every = [v for values in bands.values() for v in values]
        out.append(
            SignalByBand(
                signal=signal,
                by_band={b: round(sum(v) / len(v), 3) for b, v in sorted(bands.items()) if v},
                overall=round(sum(every) / len(every), 3) if every else 0.0,
            )
        )
    return out


def own_versus_field(conn: sqlite3.Connection, segment: Segment) -> dict[str, Any]:
    """The operator's own companies beside the field they are not counted in.

    **Deliberately not in the export, and not on the page.** It was both, and
    the panel it fed was useless for a reason worth writing down: on three of
    its four measures the field's median is *zero*. Of 139 companies, 100
    publish no case study, 113 name no client, and 113 ship no product. A
    comparison against zero tells you nothing, and "you are above the median"
    is a boast about clearing a bar on the floor.

    That is a real finding about this market rather than a defect here — but it
    means a useful version needs a different framing. Something like "you have
    more reference projects than 84% of the field", or a count of firms you are
    ahead of, both of which stay informative when most of the distribution is
    stacked at zero. Kept because the query is right and tested; rewire it only
    together with that change.
    """
    own = _own_ids(conn, segment)
    if not own:
        return {"own": [], "note": "no own_domains configured for this segment"}

    placeholders = ",".join("?" * len(own))
    rows = conn.execute(
        f"""SELECT c.id, c.domain, c.canonical_name, c.headcount_est,
                   (SELECT COUNT(*) FROM offering o WHERE o.company_id = c.id) AS offerings,
                   (SELECT COUNT(*) FROM case_study s WHERE s.company_id = c.id) AS case_studies,
                   (SELECT COUNT(*) FROM client_reference r WHERE r.company_id = c.id) AS clients,
                   (SELECT COUNT(*) FROM product p WHERE p.company_id = c.id) AS products
              FROM company c WHERE c.id IN ({placeholders})""",  # noqa: S608
        tuple(sorted(own)),
    ).fetchall()

    field_stats = collect(conn, segment)
    return {
        "own": [dict(r) for r in rows],
        "field": {
            "companies": field_stats.compared,
            "totals": field_stats.totals,
            "services": [asdict(c) for c in field_stats.services[:12]],
            "signals": [asdict(s) for s in field_stats.signals],
        },
    }
