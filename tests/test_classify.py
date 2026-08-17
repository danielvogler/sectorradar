"""Tiering, and the rules that keep it honest.

Two behaviours carry the weight here: a human's decision is never quietly
overwritten by a later run, and the model may pick new *values* within a facet
but may never invent a facet.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sectorradar import classify, db
from sectorradar.config import Segment, Settings
from sectorradar.llm import Structured, Usage
from sectorradar.models import Classification

SEGMENT = Segment.model_validate(
    {
        "slug": "test-seg",
        "name": "Test",
        "geo": {"country": "CH"},
        "inclusion": "Include a company if it builds LLM agents for clients as a named service.",
        "tiers": {
            1: "agents are the primary offering",
            2: "broader consultancy that also ships agents",
        },
        "facets": {"service_type": ["agent_dev", "workshops"]},
    }
)


class FakeLLM:
    model = "fake-model"

    def __init__(self, *results: Classification | None) -> None:
        self._results = list(results)
        self.calls = 0
        self.prompts: list[str] = []

    def structured(self, prompt: str, schema: type, *, temperature: float = 0.0):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.prompts.append(prompt)
        value = self._results.pop(0) if self._results else None
        return Structured(
            value=value, usage=Usage(input_tokens=80, output_tokens=40, model=self.model)
        )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(contact="test@example.ch", db_path=tmp_path / "radar.db")


def _company(conn: sqlite3.Connection, domain: str = "acme.ch") -> int:
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(
        conn, domain=domain, canonical_name=domain, one_liner="Builds agents."
    )
    db.upsert_membership(conn, segment_slug=SEGMENT.slug, company_id=company_id)
    conn.commit()
    return company_id


def _decision(**over: object) -> Classification:
    base: dict[str, object] = {
        "tier": 1,
        "tier_rationale": "Sells agent development as a named service.",
        "relevance": 0.9,
        "facets": {"service_type": ["agent_dev"]},
    }
    return Classification.model_validate({**base, **over})


# --- the prompt -------------------------------------------------------------


def test_the_inclusion_rule_is_injected_verbatim(conn: sqlite3.Connection) -> None:
    """The boundary lives in the YAML. Paraphrasing it in code would let it drift."""
    _company(conn)
    prompt = classify.build_prompt(SEGMENT, "acme.ch", "Builds agents.", [], [])
    assert SEGMENT.inclusion.strip() in prompt


def test_every_tier_description_reaches_the_prompt() -> None:
    prompt = classify.build_prompt(SEGMENT, "acme.ch", None, [], [])
    for text in SEGMENT.tiers.values():
        assert text in prompt


def test_internal_fields_are_hidden_from_the_prompt(conn: sqlite3.Connection) -> None:
    """`_pages_signature` is bookkeeping, not evidence about the company."""
    company_id = _company(conn)
    conn.execute(
        """
        INSERT INTO company_field
          (company_id, field, value, source_url, extractor, extracted_at)
        VALUES (?, '_pages_signature', 'abc123', 'https://acme.ch/', 'test', '2026-01-01')
        """,
        (company_id,),
    )
    facts = conn.execute("SELECT field, value FROM company_field").fetchall()
    prompt = classify.build_prompt(SEGMENT, "acme.ch", None, [], facts)
    assert "_pages_signature" not in prompt


def test_a_company_with_no_offerings_says_so_plainly() -> None:
    prompt = classify.build_prompt(SEGMENT, "acme.ch", None, [], [])
    assert "none recorded" in prompt.lower()


# --- the stage --------------------------------------------------------------


def test_classify_writes_tier_and_rationale(conn: sqlite3.Connection, settings: Settings) -> None:
    company_id = _company(conn)
    report = classify.classify(conn, SEGMENT, settings, FakeLLM(_decision()))

    assert report.classified == 1
    assert report.by_tier == {1: 1}

    row = conn.execute(
        "SELECT tier, tier_rationale, relevance FROM membership WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    assert row["tier"] == 1
    assert "named service" in row["tier_rationale"]
    assert row["relevance"] == pytest.approx(0.9)


def test_an_undecided_tier_is_recorded_as_undecided(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """`null` must be an available answer, or the model invents a tier to fill the gap."""
    _company(conn)
    report = classify.classify(
        conn,
        SEGMENT,
        settings,
        FakeLLM(_decision(tier=None, tier_rationale="Too little evidence.")),
    )
    assert report.undecided == 1
    assert report.classified == 0


def test_facet_values_are_stored_as_tags(conn: sqlite3.Connection, settings: Settings) -> None:
    company_id = _company(conn)
    classify.classify(conn, SEGMENT, settings, FakeLLM(_decision()))

    tags = conn.execute(
        "SELECT facet, value FROM tag WHERE company_id = ?", (company_id,)
    ).fetchall()
    assert [(t["facet"], t["value"]) for t in tags] == [("service_type", "agent_dev")]


def test_a_new_value_inside_a_known_facet_is_allowed(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """Facets are fixed; values are open, so the vocabulary can grow from evidence."""
    company_id = _company(conn)
    classify.classify(
        conn, SEGMENT, settings, FakeLLM(_decision(facets={"service_type": ["evals"]}))
    )
    tags = conn.execute("SELECT value FROM tag WHERE company_id = ?", (company_id,)).fetchall()
    assert [t["value"] for t in tags] == ["evals"]


def test_an_invented_facet_is_refused(conn: sqlite3.Connection, settings: Settings) -> None:
    """A new facet is a configuration change, not something a model decides mid-run."""
    company_id = _company(conn)
    classify.classify(
        conn, SEGMENT, settings, FakeLLM(_decision(facets={"made_up_facet": ["nonsense"]}))
    )
    tags = conn.execute(
        "SELECT COUNT(*) AS n FROM tag WHERE company_id = ?", (company_id,)
    ).fetchone()
    assert tags["n"] == 0


def test_a_reviewed_company_is_left_alone(conn: sqlite3.Connection, settings: Settings) -> None:
    """An hour of human review must not evaporate on the next pipeline run."""
    company_id = _company(conn)
    db.set_review(
        conn,
        segment_slug=SEGMENT.slug,
        company_id=company_id,
        review_state="accepted",
        reviewed_by="owner",
        tier=2,
    )

    llm = FakeLLM(_decision(tier=1))
    report = classify.classify(conn, SEGMENT, settings, llm)

    assert llm.calls == 0, "a reviewed company should not even be sent to the model"
    assert report.skipped_reviewed == 1
    row = conn.execute("SELECT tier FROM membership WHERE company_id = ?", (company_id,)).fetchone()
    assert row["tier"] == 2


def test_force_reclassifies_a_reviewed_company(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """Deliberate re-tiering stays possible; it just has to be asked for."""
    company_id = _company(conn)
    db.set_review(
        conn,
        segment_slug=SEGMENT.slug,
        company_id=company_id,
        review_state="accepted",
        reviewed_by="owner",
        tier=2,
    )

    llm = FakeLLM(_decision(tier=1))
    classify.classify(conn, SEGMENT, settings, llm, force=True)
    assert llm.calls == 1


def test_a_model_returning_nothing_is_counted_not_fatal(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    _company(conn)
    report = classify.classify(conn, SEGMENT, settings, FakeLLM(None))
    assert report.failed == 1


def test_cost_is_accumulated(conn: sqlite3.Connection, settings: Settings) -> None:
    _company(conn)
    report = classify.classify(conn, SEGMENT, settings, FakeLLM(_decision()))
    assert report.usage.input_tokens == 80
    assert report.usage.cost_usd > 0


def test_dry_run_writes_nothing(conn: sqlite3.Connection, settings: Settings) -> None:
    company_id = _company(conn)
    report = classify.classify(conn, SEGMENT, settings, FakeLLM(_decision()), dry_run=True)

    assert report.classified == 1
    row = conn.execute("SELECT tier FROM membership WHERE company_id = ?", (company_id,)).fetchone()
    assert row["tier"] is None
