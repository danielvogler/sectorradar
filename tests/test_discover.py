"""Discovery orchestration and the seeds source."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from sectorradar import discover as discover_mod
from sectorradar.config import Segment, Settings
from sectorradar.models import Candidate
from sectorradar.sources import SOURCES, Ctx, seeds


def _segment(**sources: object) -> Segment:
    return Segment.model_validate(
        {
            "slug": "test-seg",
            "name": "Test",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that sell widgets as a named service.",
            "tiers": {1: "primary"},
            "sources": sources,
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(contact="test@example.ch", db_path=tmp_path / "radar.db")


# --- the seeds source -------------------------------------------------------


def test_seeds_accepts_bare_url_strings(settings: Settings) -> None:
    segment = _segment(seeds={"enabled": True, "urls": ["https://a.ch", "https://b.ch"]})
    found = list(seeds.run(segment, Ctx(settings=settings)))
    assert [c.raw_url for c in found] == ["https://a.ch", "https://b.ch"]


def test_seeds_accepts_mappings_with_curator_knowledge(settings: Settings) -> None:
    """A hand-curated list knows things no automated source does."""
    segment = _segment(
        seeds={
            "enabled": True,
            "urls": [{"url": "https://a.ch", "name": "A AG", "city": "Zürich", "canton": "ZH"}],
        }
    )
    found = list(seeds.run(segment, Ctx(settings=settings)))
    assert found[0].raw_name == "A AG"
    assert found[0].raw_city == "Zürich"
    assert found[0].raw_canton == "ZH"


def test_seeds_skips_an_entry_with_no_url(settings: Settings) -> None:
    segment = _segment(seeds={"enabled": True, "urls": [{"name": "No URL"}, "https://a.ch", ""]})
    found = list(seeds.run(segment, Ctx(settings=settings)))
    assert len(found) == 1


def test_seeds_respects_the_limit(settings: Settings) -> None:
    segment = _segment(seeds={"enabled": True, "urls": [f"https://{i}.ch" for i in range(10)]})
    found = list(seeds.run(segment, Ctx(settings=settings, limit=3)))
    assert len(found) == 3


def test_seeds_on_an_empty_list_yields_nothing(settings: Settings) -> None:
    segment = _segment(seeds={"enabled": True, "urls": []})
    assert list(seeds.run(segment, Ctx(settings=settings))) == []


# --- orchestration ----------------------------------------------------------


def test_discover_writes_candidates_and_a_run_row(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    segment = _segment(seeds={"enabled": True, "urls": ["https://a.ch", "https://b.ch"]})
    report = discover_mod.discover(conn, segment, settings)

    assert report.total_found == 2
    assert report.total_new == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 2

    run = conn.execute("SELECT * FROM discovery_run").fetchone()
    assert run["source"] == "seeds"
    assert run["results_n"] == 2
    assert run["new_unique_n"] == 2
    assert run["error"] is None


def test_rerunning_discovery_finds_nothing_new(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """new_unique is what says whether a channel is exhausted, so it must be honest."""
    segment = _segment(seeds={"enabled": True, "urls": ["https://a.ch"]})
    discover_mod.discover(conn, segment, settings)
    second = discover_mod.discover(conn, segment, settings)

    assert second.total_found == 1
    assert second.total_new == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 1


def test_the_same_firm_spelled_differently_is_not_counted_twice(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    segment = _segment(
        seeds={
            "enabled": True,
            "urls": ["https://www.a.ch/services", "http://a.ch", "https://a.ch/?utm=x"],
        }
    )
    report = discover_mod.discover(conn, segment, settings)

    assert report.total_found == 3
    assert report.total_new == 1


def test_disabled_sources_are_skipped(conn: sqlite3.Connection, settings: Settings) -> None:
    segment = _segment(seeds={"enabled": False, "urls": ["https://a.ch"]})
    report = discover_mod.discover(conn, segment, settings)
    assert report.total_found == 0


def test_naming_a_source_explicitly_overrides_the_enabled_flag(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """`--source seeds` means run it, whatever the YAML says."""
    segment = _segment(seeds={"enabled": False, "urls": ["https://a.ch"]})
    report = discover_mod.discover(conn, segment, settings, sources=["seeds"])
    assert report.total_found == 1


def test_an_unknown_source_is_rejected_before_any_work(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    segment = _segment(seeds={"enabled": True, "urls": ["https://a.ch"]})
    with pytest.raises(ValueError, match="unknown source"):
        discover_mod.discover(conn, segment, settings, sources=["nope"])


def test_one_failing_source_does_not_discard_the_others(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rate limit on one channel must not throw away what another already found."""

    def exploding(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
        yield Candidate(segment_slug=segment.slug, source="boom", raw_url="https://x.ch")
        msg = "upstream said no"
        raise RuntimeError(msg)

    monkeypatch.setitem(SOURCES, "boom", exploding)
    segment = _segment(seeds={"enabled": True, "urls": ["https://a.ch"]}, boom={"enabled": True})

    report = discover_mod.discover(conn, segment, settings)

    assert len(report.errors) == 1
    assert "upstream said no" in (report.errors[0].error or "")
    # The seeds source still ran and its candidate was kept.
    domains = {r["raw_url"] for r in conn.execute("SELECT raw_url FROM candidate")}
    assert "https://a.ch" in domains
    assert "https://x.ch" in domains, "work done before the failure should survive"


def test_the_failure_is_recorded_on_the_run_row(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exploding(segment: Segment, ctx: Ctx) -> Iterator[Candidate]:
        if segment:  # always true; keeps this a generator function
            msg = "429 Too Many Requests"
            raise RuntimeError(msg)
        yield Candidate(segment_slug="x", source="boom")  # pragma: no cover

    monkeypatch.setitem(SOURCES, "boom", exploding)
    segment = _segment(boom={"enabled": True})
    discover_mod.discover(conn, segment, settings)

    row = conn.execute("SELECT error FROM discovery_run WHERE source = 'boom'").fetchone()
    assert "429" in row["error"]


def test_dry_run_writes_nothing(conn: sqlite3.Connection, settings: Settings) -> None:
    segment = _segment(seeds={"enabled": True, "urls": ["https://a.ch"]})
    report = discover_mod.discover(conn, segment, settings, dry_run=True)

    assert report.total_new == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0
