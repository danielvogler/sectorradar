"""Searching until it stops finding new companies, not until somebody stops asking.

The behaviour under test is a loop with ceilings, and the properties that
matter are the ones that stop it: it must halt when a pass stops producing,
halt at the round cap, halt at the spend cap, and — the one that makes it
honest — say which of those happened. A loop that stops at its cap while the
market is still giving has not finished, and reporting that as "done" is the
failure this whole module exists to prevent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from sectorradar import db
from sectorradar import deepen as deepen_mod
from sectorradar import discover as discover_mod
from sectorradar.config import Segment, Settings
from sectorradar.llm import Structured, Usage


def _segment(queries: list[str] | None = None) -> Segment:
    return Segment.model_validate(
        {
            "slug": "test-seg",
            "name": "Test market, Somewhere",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that audit AI systems for clients.",
            "tiers": {1: "primary"},
            "sources": {"websearch": {"enabled": True, "queries": queries or ["a query"]}},
        }
    )


@dataclass
class FakeClient:
    """Proposes a fresh batch of queries each time it is asked."""

    model: str = "fake"
    calls: int = 0

    def structured(self, prompt: str, schema: type, *, temperature: float = 0.0):  # type: ignore[no-untyped-def]
        self.calls += 1
        return Structured(
            value=schema(queries=(f"new query {self.calls}a", f"new query {self.calls}b")),
            usage=Usage(input_tokens=10, output_tokens=10, model="fake"),
        )


@dataclass
class ExhaustedClient:
    """Can think of nothing that has not been tried."""

    model: str = "fake"

    def structured(self, prompt: str, schema: type, *, temperature: float = 0.0):  # type: ignore[no-untyped-def]
        return Structured(value=schema(queries=()), usage=Usage(0, 0, "fake"))


def _fake_discovery(monkeypatch: pytest.MonkeyPatch, pattern: list[tuple[int, int]]) -> list[int]:
    """Make each round return a scripted (found, new) pair."""
    seen: list[int] = []

    def fake(conn, segment, settings, *, sources=None, limit=None, dry_run=False):  # type: ignore[no-untyped-def]
        from sectorradar.discover import DiscoveryReport, SourceResult

        index = min(len(seen), len(pattern) - 1)
        found, new = pattern[index]
        seen.append(index)
        report = DiscoveryReport(segment=segment.slug)
        report.results.append(SourceResult(source="websearch", found=found, new_unique=new))
        return report

    monkeypatch.setattr(discover_mod, "discover", fake)
    return seen


def _settings() -> Settings:
    return Settings(contact="a@b.ch")


def test_it_stops_when_a_pass_stops_producing(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_discovery(monkeypatch, [(100, 60), (100, 2)])
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")

    report = deepen_mod.deepen(conn, _segment(), _settings(), FakeClient())

    assert "saturated" in report.stopped_because
    assert len(report.rounds) == 2


def test_stopping_at_the_cap_while_still_productive_says_so(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honest failure. A capped run is not a finished one."""
    _fake_discovery(monkeypatch, [(100, 70)])
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")

    report = deepen_mod.deepen(conn, _segment(), _settings(), FakeClient(), max_rounds=3)

    assert "round cap" in report.stopped_because
    assert "more in it" in report.stopped_because
    assert len(report.rounds) == 3


def test_every_round_widens_the_queries_rather_than_repeating_them(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synonym returns the same companies and wastes a round."""
    _fake_discovery(monkeypatch, [(100, 70)])
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")

    report = deepen_mod.deepen(conn, _segment(), _settings(), FakeClient(), max_rounds=3)

    assert len(report.queries_invented) == len(set(report.queries_invented))
    assert "a query" not in report.queries_invented


def test_it_gives_up_when_no_new_angle_can_be_found(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_discovery(monkeypatch, [(100, 70)])
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")

    report = deepen_mod.deepen(conn, _segment(), _settings(), ExhaustedClient(), max_rounds=5)

    assert "no new angles" in report.stopped_because


def test_the_ceilings_are_honoured(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An autonomous loop with no bound is a bill with no bound."""
    _fake_discovery(monkeypatch, [(100, 90)])
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")

    report = deepen_mod.deepen(conn, _segment(), _settings(), FakeClient(), max_rounds=2)

    assert len(report.rounds) == 2


def test_the_report_names_the_queries_worth_keeping(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The segment file is never rewritten — which queries earned their keep is
    a judgement for whoever owns it."""
    _fake_discovery(monkeypatch, [(100, 70), (100, 1)])
    db.upsert_segment(conn, "test-seg", "Test", "slug: test-seg")

    rendered = deepen_mod.deepen(conn, _segment(), _settings(), FakeClient()).render()

    assert "queries worth keeping" in rendered
    assert "new query 1a" in rendered


def test_the_expansion_prompt_asks_for_angles_not_synonyms() -> None:
    prompt = deepen_mod.build_expansion_prompt(
        _segment(), tried=["KI Audit Schweiz"], cities=["Zürich"], names=["Acme"], want=8
    )

    assert "would **miss**" in prompt
    assert "Do not paraphrase" in prompt
    # It has to know what has been found, or it cannot steer away from it.
    assert "Zürich" in prompt
    assert "Acme" in prompt
    assert "KI Audit Schweiz" in prompt


# --- remembering between runs ------------------------------------------------


def test_a_second_run_starts_from_what_the_first_invented(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise "saturated" is a claim about ten queries, not about a market.

    This is what a real first use hit: `deepen` stopped at its round cap while
    still finding 41% new, the operator ran it again, and the second run saw
    only the segment file's own queries — found nothing, because the first run
    had already taken them — and reported the market exhausted.
    """
    db.upsert_segment(conn, "test-seg", "Test market, Somewhere", "slug: test-seg")
    _fake_discovery(monkeypatch, [(100, 70)])
    deepen_mod.deepen(conn, _segment(), _settings(), FakeClient(), max_rounds=2)

    second = deepen_mod.deepen(conn, _segment(), _settings(), FakeClient(), max_rounds=1)

    assert second.recalled > 0
    assert "reused" in second.render()


def test_what_was_invented_survives_the_process(conn: sqlite3.Connection) -> None:
    deepen_mod.remember(conn, "test-seg", ["query one", "query two"])

    assert deepen_mod.learned(conn, "test-seg") == ["query two", "query one"]


def test_remembering_the_same_query_twice_keeps_one(conn: sqlite3.Connection) -> None:
    deepen_mod.remember(conn, "test-seg", ["a query"])
    deepen_mod.remember(conn, "test-seg", ["a query"])

    assert deepen_mod.learned(conn, "test-seg") == ["a query"]


def test_one_segment_does_not_inherit_another_segments_queries(
    conn: sqlite3.Connection,
) -> None:
    deepen_mod.remember(conn, "seg-a", ["a query"])

    assert deepen_mod.learned(conn, "seg-b") == []


def test_a_dry_run_invents_without_remembering(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.upsert_segment(conn, "test-seg", "Test market, Somewhere", "slug: test-seg")
    _fake_discovery(monkeypatch, [(100, 70)])

    deepen_mod.deepen(conn, _segment(), _settings(), FakeClient(), max_rounds=2, dry_run=True)

    assert deepen_mod.learned(conn, "test-seg") == []
