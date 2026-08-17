"""Discovery sources.

Every source is a callable with the same shape, registered in :data:`SOURCES`
under the name used in a segment's YAML. Adding a channel means adding a module
and one registry entry — never a change to ``discover.py``.

    def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sectorradar.config import Segment, Settings
from sectorradar.models import Candidate

if TYPE_CHECKING:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Ctx:
    """Everything a source is allowed to depend on.

    Passing this rather than letting sources read the environment keeps them
    testable and makes their dependencies visible at the call site.
    """

    settings: Settings
    limit: int | None = None
    #: Queries already run in this session, so a source can avoid repeating work.
    seen_queries: set[str] = field(default_factory=set)


SourceFn = Callable[[Segment, Ctx], Iterator[Candidate]]

SOURCES: dict[str, SourceFn] = {}


def register(name: str, fn: SourceFn) -> None:
    SOURCES[name] = fn


def available() -> list[str]:
    return sorted(SOURCES)


def _install() -> None:
    """Import and register the built-in sources.

    Done in a function rather than at module scope so that a source failing to
    import (a missing optional dependency, say) is a clear error at startup
    rather than a mysteriously absent registry entry.
    """
    from sectorradar.sources import directories, jobads, lindas, seeds, websearch

    register("seeds", seeds.run)
    register("websearch", websearch.run)
    register("jobads", jobads.run)
    register("directories", directories.run)
    register("lindas", lindas.run)


_install()

__all__ = ["SOURCES", "Candidate", "Ctx", "SourceFn", "available", "register"]
