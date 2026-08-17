"""Run discovery sources and record what they found.

Each source contributes ``candidate`` rows and one ``discovery_run`` row per
query, carrying ``new_unique_n`` — how many candidates that query produced that
nothing had seen before. That number, not the raw result count, is what tells
you whether a channel is still worth querying.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sectorradar import db, resolve
from sectorradar.config import Segment, Settings
from sectorradar.logging import get_logger
from sectorradar.sources import SOURCES, Ctx

log = get_logger(__name__)

#: Consecutive low-yield queries within one source before it is abandoned.
SATURATION_WINDOW = 10
#: Below this share of new-unique results, a query counts as low yield.
SATURATION_RATIO = 0.05


@dataclass
class SourceResult:
    source: str
    found: int = 0
    new_unique: int = 0
    error: str | None = None


@dataclass
class DiscoveryReport:
    segment: str
    results: list[SourceResult] = field(default_factory=list)

    @property
    def total_found(self) -> int:
        return sum(r.found for r in self.results)

    @property
    def total_new(self) -> int:
        return sum(r.new_unique for r in self.results)

    @property
    def errors(self) -> list[SourceResult]:
        return [r for r in self.results if r.error]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _known_keys(conn: sqlite3.Connection, segment_slug: str) -> set[str]:
    """Every candidate identity already recorded for this segment.

    Keyed on the normalised domain so that the same firm arriving from a second
    source with a different URL spelling is not counted as new.
    """
    rows = conn.execute(
        "SELECT raw_url FROM candidate WHERE segment_slug = ?", (segment_slug,)
    ).fetchall()
    keys = set()
    for row in rows:
        domain = resolve.normalise_domain(row["raw_url"])
        keys.add(domain or f"raw:{row['raw_url']}")
    return keys


def discover(
    conn: sqlite3.Connection,
    segment: Segment,
    settings: Settings,
    *,
    sources: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> DiscoveryReport:
    """Run the requested sources, or every enabled one."""
    db.upsert_segment(conn, segment.slug, segment.name, segment.to_yaml())

    explicit = sources is not None
    requested = sources if sources is not None else segment.enabled_sources()
    unknown = [name for name in requested if name not in SOURCES]

    if unknown and explicit:
        # The caller named a source that does not exist — almost always a typo
        # on `--source`, and worth failing on so it is not silently ignored.
        msg = f"unknown source(s): {', '.join(unknown)}. Available: {', '.join(sorted(SOURCES))}"
        raise ValueError(msg)

    if unknown:
        # A segment YAML enabling a source this build does not carry is a
        # different situation: the config is describing a channel that has not
        # been implemented yet, or has been removed. Warn and run the rest,
        # rather than making one stale line in a YAML file abort the pipeline.
        log.warning(
            "discover.unknown_sources_skipped",
            skipped=unknown,
            available=sorted(SOURCES),
            segment=segment.slug,
        )
        requested = [name for name in requested if name in SOURCES]

    report = DiscoveryReport(segment=segment.slug)
    known = _known_keys(conn, segment.slug)
    ctx = Ctx(settings=settings, limit=limit)

    for name in requested:
        if not segment.source(name).enabled and sources is None:
            continue

        result = SourceResult(source=name)
        started = _now()

        try:
            for candidate in SOURCES[name](segment, ctx):
                result.found += 1
                key = resolve.normalise_domain(candidate.raw_url) or f"raw:{candidate.raw_url}"
                is_new = key not in known
                if is_new:
                    known.add(key)
                    result.new_unique += 1
                    db.insert_candidate(
                        conn,
                        segment_slug=segment.slug,
                        source=name,
                        raw_name=candidate.raw_name,
                        raw_url=candidate.raw_url,
                        source_detail=candidate.source_detail,
                        discovered_at=candidate.discovered_at.isoformat(timespec="seconds"),
                        raw_city=candidate.raw_city,
                        raw_canton=candidate.raw_canton,
                    )
        except Exception as exc:
            # Deliberately broad. One source going down — a rate limit, a
            # changed page structure, a DNS blip — must not discard what the
            # other sources already found. The failure is recorded on the
            # discovery_run row and surfaced in the CLI's exit code.
            result.error = f"{type(exc).__name__}: {exc}"
            log.error("discover.source_failed", source=name, error=result.error)

        conn.execute(
            """
            INSERT INTO discovery_run
              (segment_slug, source, query, results_n, new_unique_n, cost_usd,
               started_at, finished_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment.slug,
                name,
                None,
                result.found,
                result.new_unique,
                0.0,
                started,
                _now(),
                result.error,
            ),
        )
        report.results.append(result)
        log.info(
            "discover.source_done",
            source=name,
            found=result.found,
            new=result.new_unique,
            error=result.error,
        )

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    return report
