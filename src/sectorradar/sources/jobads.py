"""Job advertisements — the earliest signal there is.

Swiss agencies staff up for agent work months before they market it. A firm
advertising for a LangGraph engineer in March is selling agent development by
September, and it will not appear in any "top AI consultancies" listicle until
the year after. §9 ranks this the highest-yield channel for tier 1, and this
module exists because of that ranking rather than because job boards are
pleasant to work with.

**How it reads job boards matters.** It does not crawl jobs.ch. Job boards
carry personal data, they have terms of service restricting scraping, and they
bot-block hard. Instead it asks the configured search provider for postings
matching each keyword and keeps only the *employer's own domain* — which is the
one field of a job ad this project has any use for. The advert itself, its
text, and above all the named contact person are never stored.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from sectorradar.config import ConfigError, Segment
from sectorradar.logging import get_logger
from sectorradar.models import Candidate
from sectorradar.sources import Ctx
from sectorradar.sources.websearch import get_provider

log = get_logger(__name__)

NAME = "jobads"

#: Swiss job boards worth asking about. Used to shape the query, not crawled.
BOARDS: tuple[str, ...] = ("jobs.ch", "jobscout24.ch", "indeed.ch", "linkedin.com/jobs")

RESULTS_PER_KEYWORD = 20


def _query(keyword: str) -> str:
    boards = " OR ".join(f"site:{board}" for board in BOARDS)
    return f'"{keyword}" Stelle Schweiz ({boards}) OR "Jobs Schweiz" "{keyword}"'


def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
    """Yield the employers behind postings matching the segment's keywords."""
    config = segment.source(NAME)
    keywords: list[str] = list(getattr(config, "keywords", None) or [])
    if not keywords:
        log.warning("jobads.no_keywords", segment=segment.slug)
        return

    try:
        provider = get_provider(ctx.settings)
    except ConfigError as exc:
        log.warning("jobads.no_search_provider", error=str(exc))
        return

    emitted = 0
    for keyword in keywords:
        if ctx.limit is not None and emitted >= ctx.limit:
            return
        try:
            hits = provider.search(_query(keyword), RESULTS_PER_KEYWORD)
        except (httpx.HTTPError, ConfigError) as exc:
            log.warning("jobads.query_failed", keyword=keyword, error=str(exc))
            continue

        log.info("jobads.keyword", keyword=keyword, hits=len(hits))
        for hit in hits:
            if ctx.limit is not None and emitted >= ctx.limit:
                return
            emitted += 1
            yield Candidate(
                segment_slug=segment.slug,
                source=NAME,
                raw_url=hit.url,
                raw_name=hit.title,
                # The keyword, not the advert. Nothing about the posting or the
                # person who placed it is recorded.
                source_detail=f"hiring signal: {keyword}",
            )
