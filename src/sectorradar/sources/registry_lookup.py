"""Turn registered company names into websites.

The commercial register knows every company in the country and records no
website for any of them. Web search knows websites and only surfaces companies
that rank. Neither channel alone finds a small consultancy that is registered,
trading, and invisible to search — which is most of them.

This closes the gap: take the names the registry sweep produced, ask the search
provider for each one's own site, and keep the answer only when the domain
plausibly corresponds to the name. That last check is the whole difficulty. A
search for "Muster Consulting GmbH" will happily return a directory listing, a
competitor, or a news article; accepting those would fill the database with
confident nonsense, which is worse than the gap it was meant to close.

Measured motivation: two companies the owner named from memory —
including his own — had never appeared as a candidate through any channel.
Search had simply never surfaced them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from rapidfuzz import fuzz

from sectorradar.config import ConfigError, Segment
from sectorradar.logging import get_logger
from sectorradar.models import Candidate
from sectorradar.resolve import name_keys, normalise_domain, normalise_name
from sectorradar.sources import Ctx
from sectorradar.sources.websearch import get_provider

log = get_logger(__name__)

NAME = "registry_lookup"

#: How closely the domain has to echo the company name to be believed.
#: Deliberately strict — a wrong domain attaches a real company's registry
#: identity to somebody else's website, which is unrecoverable by inspection.
MATCH_THRESHOLD = 82

RESULTS_PER_NAME = 5


@dataclass(frozen=True)
class Match:
    name: str
    domain: str
    score: int


def domain_tokens(domain: str) -> str:
    """The distinctive part of a domain, as words.

    ``muster-consulting.ch`` becomes ``muster consulting``: drop the public
    suffix, split on hyphens, and leave the rest for fuzzy comparison.
    """
    host = domain.removeprefix("www.")
    stem = host.split(".")[0]
    return " ".join(stem.replace("-", " ").replace("_", " ").split())


def score_match(company_name: str, domain: str) -> int:
    """How strongly a domain corresponds to a registered company name.

    Compared against every folding of the name that :mod:`resolve` recognises,
    so ``zuercher-ki.ch`` still matches "Zürcher KI GmbH".
    """
    candidate = domain_tokens(domain)
    if not candidate:
        return 0

    best = 0
    for key in name_keys(company_name) or {normalise_name(company_name)}:
        if not key:
            continue
        # token_set_ratio, because a domain routinely drops a word the
        # registered name carries ("Muster Consulting" -> muster.ch).
        best = max(best, int(fuzz.token_set_ratio(key, candidate)))
        if key.replace(" ", "") == candidate.replace(" ", ""):
            return 100
    return best


def unresolved_registry_names(conn: sqlite3.Connection, segment: Segment, limit: int) -> list[str]:
    """Registry candidates that never became a company, newest first."""
    rows = conn.execute(
        """
        SELECT DISTINCT raw_name
          FROM candidate
         WHERE segment_slug = ?
           AND source = 'lindas'
           AND raw_name IS NOT NULL
           AND resolved_to IS NULL
      ORDER BY id DESC
         LIMIT ?
        """,
        (segment.slug, limit),
    ).fetchall()
    return [str(r["raw_name"]) for r in rows]


def find_domain(provider: object, company_name: str, country: str) -> Match | None:
    """Search for one company's own website, or return None."""
    query = f'"{company_name}" {country} official website'
    try:
        hits = provider.search(query, RESULTS_PER_NAME)  # type: ignore[attr-defined]
    except (httpx.HTTPError, ConfigError) as exc:
        log.warning("registry_lookup.query_failed", name=company_name, error=str(exc))
        return None

    best: Match | None = None
    for hit in hits:
        domain = normalise_domain(hit.url)
        if domain is None:
            continue
        score = score_match(company_name, domain)
        if score >= MATCH_THRESHOLD and (best is None or score > best.score):
            best = Match(name=company_name, domain=domain, score=score)
    return best


def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
    """Yield a candidate per registry name whose website could be identified.

    Requires a database connection on the context, because unlike every other
    source this one reads what previous runs found rather than going out cold.
    """
    conn = getattr(ctx, "conn", None)
    if conn is None:
        log.warning("registry_lookup.no_connection")
        return

    config = segment.source(NAME)
    budget = int(getattr(config, "max_lookups", 0) or ctx.limit or 150)

    names = unresolved_registry_names(conn, segment, budget)
    if not names:
        log.info("registry_lookup.nothing_to_resolve", segment=segment.slug)
        return

    try:
        provider = get_provider(ctx.settings)
    except ConfigError as exc:
        log.warning("registry_lookup.no_search_provider", error=str(exc))
        return

    found = 0
    for company_name in names:
        match = find_domain(provider, company_name, segment.geo.country)
        if match is None:
            log.debug("registry_lookup.unmatched", name=company_name)
            continue
        found += 1
        log.info(
            "registry_lookup.matched", name=company_name, domain=match.domain, score=match.score
        )
        yield Candidate(
            segment_slug=segment.slug,
            source=NAME,
            raw_name=company_name,
            raw_url=f"https://{match.domain}",
            source_detail=f"registry name matched to domain (confidence {match.score})",
        )

    log.info("registry_lookup.done", tried=len(names), matched=found)
