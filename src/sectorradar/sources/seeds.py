"""Hand-curated seeds from the segment YAML.

The highest-precision channel there is, and for tier 1 it beats every automated
one: the owner's own referral graph and the "we also considered X" list from
lost pitches are things no search engine knows.

An entry is either a bare URL string or a mapping that also carries what the
curator knows:

.. code-block:: yaml

    seeds:
      enabled: true
      urls:
        - https://example.ch
        - url: https://other.ch
          name: Other Consulting AG
          city: Zürich
          canton: ZH
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sectorradar.config import Segment
from sectorradar.logging import get_logger
from sectorradar.models import Candidate
from sectorradar.sources import Ctx

log = get_logger(__name__)

NAME = "seeds"


def _entry_to_candidate(entry: Any, segment: Segment) -> Candidate | None:
    if isinstance(entry, str):
        url = entry.strip()
        if not url:
            return None
        return Candidate(
            segment_slug=segment.slug, source=NAME, raw_url=url, source_detail="segment yaml"
        )

    if isinstance(entry, dict):
        url = str(entry.get("url", "")).strip()
        if not url:
            log.warning("seeds.entry_without_url", entry=entry)
            return None
        return Candidate(
            segment_slug=segment.slug,
            source=NAME,
            raw_url=url,
            raw_name=entry.get("name"),
            raw_city=entry.get("city"),
            raw_canton=entry.get("canton"),
            source_detail=str(entry.get("note") or "segment yaml"),
        )

    log.warning("seeds.unusable_entry", entry=repr(entry))
    return None


def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
    """Yield one candidate per seed entry."""
    config = segment.source(NAME)
    entries: list[Any] = list(getattr(config, "urls", None) or [])

    if not entries:
        log.warning("seeds.empty", segment=segment.slug)
        return

    emitted = 0
    for entry in entries:
        if ctx.limit is not None and emitted >= ctx.limit:
            log.info("seeds.limit_reached", limit=ctx.limit)
            return
        candidate = _entry_to_candidate(entry, segment)
        if candidate is not None:
            emitted += 1
            yield candidate
