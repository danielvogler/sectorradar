"""The Swiss commercial register, via the LINDAS SPARQL endpoint.

Sweeps company *purpose* text (the Zweck every Swiss company files) for the
segment's terms. High recall on small GmbHs that have no marketing presence
whatsoever, and brutal precision — a generic IT purpose clause matches
thousands of firms that have never touched the subject.

**A hard limitation, stated up front:** the register records no website. Every
row this source produces therefore arrives without a domain, and ``resolve.py``
declines to promote it to a company, recording ``no usable URL`` as the reason.
That is the correct outcome, not a bug: this channel tells you a company with a
matching purpose clause *exists*, and finding its website is a separate problem.

The rows are still worth having. They are the raw material for a human working
through the long tail, and they show what a purpose sweep does and does not
buy — which is the honest answer to "should we integrate the commercial
register", and cheaper to demonstrate than to argue about.

Verified facts (August 2026):

* ``https://ld.admin.ch/query`` — POST, form-encoded, ``Accept: text/csv``.
* NOGA industry codes are no longer publicly exposed, so nothing here may
  depend on them.
* The bare host ``zefix.admin.ch`` has no DNS record; the REST API lives at
  ``www.zefix.admin.ch`` and needs credentials, which is why this uses LINDAS.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import httpx

from sectorradar.config import Segment
from sectorradar.logging import get_logger
from sectorradar.models import Candidate
from sectorradar.sources import Ctx

log = get_logger(__name__)

NAME = "lindas"
ENDPOINT = "https://ld.admin.ch/query"

#: Rows per purpose term. The pool is enormous and mostly noise, so this is a
#: deliberate ceiling rather than a page size to iterate through.
LIMIT_PER_TERM = 150

QUERY = """
PREFIX schema: <http://schema.org/>
SELECT ?company ?name ?locality WHERE {{
  ?company a schema:Organization ;
           schema:legalName ?name ;
           schema:description ?purpose .
  OPTIONAL {{ ?company schema:address/schema:addressLocality ?locality }}
  FILTER(CONTAINS(LCASE(?purpose), "{term}"))
}}
LIMIT {limit}
"""


def search_purpose(
    term: str, limit: int = LIMIT_PER_TERM, timeout: float = 120.0
) -> list[dict[str, str]]:
    """Companies whose registered purpose contains ``term``."""
    query = QUERY.format(term=term.lower().replace('"', ""), limit=limit)
    response = httpx.post(
        ENDPOINT,
        data={"query": query},
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Accept": "text/csv",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))


def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
    """Yield one candidate per matching registered company."""
    config = segment.source(NAME)
    terms: list[str] = list(getattr(config, "purpose_terms", None) or [])
    if not terms:
        log.warning("lindas.no_terms", segment=segment.slug)
        return

    emitted = 0
    for term in terms:
        if ctx.limit is not None and emitted >= ctx.limit:
            return
        try:
            rows = search_purpose(term)
        except (httpx.HTTPError, csv.Error) as exc:
            log.warning("lindas.query_failed", term=term, error=str(exc))
            continue

        log.info("lindas.term", term=term, rows=len(rows))
        for row in rows:
            if ctx.limit is not None and emitted >= ctx.limit:
                return
            name = (row.get("name") or "").strip()
            if not name:
                continue
            emitted += 1
            yield Candidate(
                segment_slug=segment.slug,
                source=NAME,
                raw_name=name,
                # No website exists in the register. Left as None deliberately
                # so resolve records an honest rejection rather than inventing
                # a domain from the company name.
                raw_url=None,
                raw_city=(row.get("locality") or "").strip() or None,
                source_detail=f"zefix purpose contains: {term}",
            )
