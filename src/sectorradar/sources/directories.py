"""Agency directories — dense, low-precision, and used carefully.

Clutch, GoodFirms, DesignRush and Sortlist all list Swiss agencies, and all of
them have terms of service restricting scraping and bot-block anything that
tries. This module does not scrape them.

It asks the configured search provider which companies each directory lists for
the segment's terms, keeps the resulting *company* domains, and discards
everything else. The company's own website is then crawled by ``fetch.py`` like
any other candidate. Directory content itself — the profiles, the star ratings,
the reviews — is never copied, which is both the licensing-safe reading of
§14 and the more useful one, since a company's own site is better evidence than
a directory's summary of it.

Expect this channel to skew heavily towards tier 3. It is enabled because
breadth is occasionally worth having, not because the results are good.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from sectorradar.config import ConfigError, Segment
from sectorradar.logging import get_logger
from sectorradar.models import Candidate
from sectorradar.resolve import normalise_domain
from sectorradar.sources import Ctx
from sectorradar.sources.websearch import get_provider

log = get_logger(__name__)

NAME = "directories"
RESULTS_PER_SITE = 20

#: What to ask each directory about. Kept generic: the directory's own
#: categories are coarse, and precision comes from `classify` later.
DEFAULT_TERMS: tuple[str, ...] = ("artificial intelligence", "AI development", "machine learning")


def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
    """Yield company domains that directories list for this segment."""
    config = segment.source(NAME)
    sites: list[str] = list(getattr(config, "sites", None) or [])
    if not sites:
        log.warning("directories.no_sites", segment=segment.slug)
        return

    terms: list[str] = list(getattr(config, "terms", None) or DEFAULT_TERMS)
    country = segment.geo.country

    try:
        provider = get_provider(ctx.settings)
    except ConfigError as exc:
        log.warning("directories.no_search_provider", error=str(exc))
        return

    emitted = 0
    for site in sites:
        for term in terms:
            if ctx.limit is not None and emitted >= ctx.limit:
                return
            query = f"site:{site} {term} {country} companies"
            try:
                hits = provider.search(query, RESULTS_PER_SITE)
            except (httpx.HTTPError, ConfigError) as exc:
                # A directory that blocks or rate-limits is expected, not
                # exceptional. Record it and move to the next one; never build
                # a way around it.
                log.warning("directories.query_failed", site=site, term=term, error=str(exc))
                continue

            kept = 0
            for hit in hits:
                if ctx.limit is not None and emitted >= ctx.limit:
                    return
                domain = normalise_domain(hit.url)
                # normalise_domain rejects the directory hosts themselves, so a
                # hit that survives is a company's own site. A hit that does not
                # is the directory talking about itself.
                if domain is None:
                    continue
                kept += 1
                emitted += 1
                yield Candidate(
                    segment_slug=segment.slug,
                    source=NAME,
                    raw_url=hit.url,
                    raw_name=hit.title,
                    source_detail=f"listed on {site} for '{term}'",
                )

            log.info("directories.site", site=site, term=term, hits=len(hits), kept=kept)
