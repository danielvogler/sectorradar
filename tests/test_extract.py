"""Extraction, and the evidence check that makes it trustworthy.

Written before ``extract.py`` existed.

The premise of this project is that every claim is checkable. A language model
reading generic marketing copy will confidently produce a plausible service
list that the page does not support, and that failure is silent — the output
looks exactly like a good one. The defence is mechanical rather than
persuasive: every quote must be found verbatim in the text the model was given,
and any claim whose quote is not found is dropped and counted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sectorradar import db, extract
from sectorradar.config import Segment, Settings
from sectorradar.llm import Structured, Usage
from sectorradar.models import CompanyProfile, Offering

SEGMENT = Segment.model_validate(
    {
        "slug": "test-seg",
        "name": "Test",
        "geo": {"country": "CH"},
        "inclusion": "Include companies that build LLM agents for clients.",
        "tiers": {1: "primary"},
        "facets": {"service_type": ["agent_dev", "workshops"]},
    }
)

PAGE_TEXT = (
    "Acme Intelligence builds LLM agents for enterprise clients across Switzerland. "
    "We also run GenAI enablement workshops for leadership teams. "
    "Founded in 2019 and based in Zurich, our team of 14 engineers ships production systems."
)
PAGE_URL = "https://acme.ch/services"


class FakeLLM:
    """A stand-in that returns whatever the test wants, and counts calls."""

    model = "fake-model"

    def __init__(self, *profiles: CompanyProfile | None) -> None:
        self._profiles = list(profiles)
        self.calls = 0
        self.prompts: list[str] = []

    def structured(self, prompt: str, schema: type, *, temperature: float = 0.0):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.prompts.append(prompt)
        value = self._profiles.pop(0) if self._profiles else None
        return Structured(
            value=value, usage=Usage(input_tokens=100, output_tokens=50, model=self.model)
        )


def _offering(quote: str, label: str = "Agent development") -> Offering:
    return Offering(label=label, evidence_quote=quote, evidence_url=PAGE_URL)


# ---------------------------------------------------------------------------
# The evidence check
# ---------------------------------------------------------------------------


def test_a_verbatim_quote_is_kept() -> None:
    assert extract.quote_is_supported("builds LLM agents for enterprise clients", PAGE_TEXT)


def test_a_fabricated_quote_is_rejected() -> None:
    """The whole point: a plausible sentence the page never contained."""
    assert not extract.quote_is_supported(
        "we offer 24/7 managed AI operations with an SLA", PAGE_TEXT
    )


def test_whitespace_differences_do_not_reject_a_real_quote() -> None:
    """Models reflow whitespace; that is not fabrication."""
    assert extract.quote_is_supported("builds   LLM\n agents  for enterprise", PAGE_TEXT)


def test_case_differences_do_not_reject_a_real_quote() -> None:
    assert extract.quote_is_supported("BUILDS LLM AGENTS", PAGE_TEXT)


def test_an_empty_quote_is_not_evidence() -> None:
    assert not extract.quote_is_supported("", PAGE_TEXT)
    assert not extract.quote_is_supported("   ", PAGE_TEXT)


def test_a_quote_stitched_from_two_sentences_is_rejected() -> None:
    """Joining distant fragments produces a claim the page never made."""
    stitched = "builds LLM agents based in Zurich"
    assert not extract.quote_is_supported(stitched, PAGE_TEXT)


# ---------------------------------------------------------------------------
# Filtering a profile
# ---------------------------------------------------------------------------


def test_unsupported_offerings_are_dropped() -> None:
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="Builds agents.",
        offerings=(
            _offering("builds LLM agents for enterprise clients"),
            _offering("we offer 24/7 managed AI operations", label="Managed ops"),
        ),
    )

    kept, dropped = extract.drop_unsupported(profile, {PAGE_URL: PAGE_TEXT})

    assert len(kept.offerings) == 1
    assert kept.offerings[0].label == "Agent development"
    assert dropped == 1


def test_an_offering_citing_a_page_that_was_never_fetched_is_dropped() -> None:
    """A URL the model invented cannot support anything."""
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="x",
        offerings=(
            Offering(
                label="Invented",
                evidence_quote="builds LLM agents for enterprise clients",
                evidence_url="https://acme.ch/never-fetched",
            ),
        ),
    )

    kept, dropped = extract.drop_unsupported(profile, {PAGE_URL: PAGE_TEXT})
    assert len(kept.offerings) == 0
    assert dropped == 1


def test_a_fully_supported_profile_loses_nothing() -> None:
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="x",
        offerings=(
            _offering("builds LLM agents for enterprise clients"),
            _offering("run GenAI enablement workshops", label="Workshops"),
        ),
    )
    kept, dropped = extract.drop_unsupported(profile, {PAGE_URL: PAGE_TEXT})
    assert len(kept.offerings) == 2
    assert dropped == 0


def test_hallucination_rate_is_reported() -> None:
    rate = extract.hallucination_rate(dropped=3, total=12)
    assert rate == pytest.approx(0.25)
    assert extract.hallucination_rate(dropped=0, total=0) == 0.0


# ---------------------------------------------------------------------------
# Personal data
# ---------------------------------------------------------------------------


def test_the_prompt_forbids_personal_data() -> None:
    """A legal constraint on what this tool may collect, so it must be stated."""
    prompt = extract.build_prompt("acme.ch", {PAGE_URL: PAGE_TEXT}, SEGMENT)
    lowered = prompt.lower()
    assert "never record information about individual people" in lowered
    assert "headcount" in lowered


def test_personal_fields_are_stripped_from_a_profile() -> None:
    """Belt and braces: the prompt says no, and the code enforces no."""
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="Led by Daniel Vogler, reachable at dan@acme.ch or +41 79 123 45 67.",
        offerings=(),
    )
    cleaned = extract.strip_personal_data(profile)
    assert "dan@acme.ch" not in cleaned.one_liner
    assert "+41 79 123 45 67" not in cleaned.one_liner


def test_an_offering_quoting_an_email_address_is_dropped() -> None:
    text = "Contact our lead engineer at anna.meier@acme.ch for agent projects."
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="x",
        offerings=(
            Offering(
                label="Agent projects",
                evidence_quote="anna.meier@acme.ch for agent projects",
                evidence_url=PAGE_URL,
            ),
        ),
    )
    kept, dropped = extract.drop_unsupported(profile, {PAGE_URL: text})
    assert len(kept.offerings) == 0, "evidence containing personal data must not be stored"
    assert dropped == 1


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(contact="test@example.ch", db_path=tmp_path / "radar.db")


def _company_with_page(conn: sqlite3.Connection, settings: Settings) -> int:
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(conn, domain="acme.ch", canonical_name="Acme")
    db.upsert_membership(conn, segment_slug=SEGMENT.slug, company_id=company_id)

    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    path = settings.raw_dir / "page.html"
    path.write_text(f"<html><body><p>{PAGE_TEXT}</p></body></html>", encoding="utf-8")
    conn.execute(
        """
        INSERT INTO page (url_sha, company_id, url, content_sha, http_status, fetched_at, path)
        VALUES ('sha1', ?, ?, 'content1', 200, '2026-01-01', ?)
        """,
        (company_id, PAGE_URL, str(path)),
    )
    conn.commit()
    return company_id


def test_extract_stores_offerings_with_provenance(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    company_id = _company_with_page(conn, settings)
    llm = FakeLLM(
        CompanyProfile(
            domain="acme.ch",
            one_liner="Builds LLM agents for Swiss enterprises.",
            offerings=(_offering("builds LLM agents for enterprise clients"),),
            founded_year=2019,
            headcount_estimate=14,
            city="Zurich",
        )
    )

    report = extract.extract(conn, SEGMENT, settings, llm)

    assert report.companies == 1
    assert report.offerings_kept == 1

    row = conn.execute("SELECT * FROM offering WHERE company_id = ?", (company_id,)).fetchone()
    assert row["evidence_url"] == PAGE_URL
    assert row["evidence_quote"]

    company = conn.execute(
        "SELECT one_liner, founded_year FROM company WHERE id = ?", (company_id,)
    ).fetchone()
    assert company["founded_year"] == 2019


def test_extract_records_the_extractor_version(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """A re-run with a new prompt must be distinguishable from the old one."""
    company_id = _company_with_page(conn, settings)
    llm = FakeLLM(
        CompanyProfile(
            domain="acme.ch",
            one_liner="x",
            offerings=(),
            founded_year=2019,
        )
    )
    extract.extract(conn, SEGMENT, settings, llm)

    row = conn.execute(
        "SELECT extractor FROM company_field WHERE company_id = ?", (company_id,)
    ).fetchone()
    assert extract.PROMPT_VERSION in row["extractor"]
    assert "fake-model" in row["extractor"]


def test_extract_counts_dropped_claims(conn: sqlite3.Connection, settings: Settings) -> None:
    _company_with_page(conn, settings)
    llm = FakeLLM(
        CompanyProfile(
            domain="acme.ch",
            one_liner="x",
            offerings=(
                _offering("builds LLM agents for enterprise clients"),
                _offering("we guarantee a 99.99% uptime SLA", label="SLA"),
            ),
        )
    )

    report = extract.extract(conn, SEGMENT, settings, llm)

    assert report.offerings_kept == 1
    assert report.offerings_dropped == 1
    assert report.hallucination_rate == pytest.approx(0.5)


def test_extract_skips_a_company_whose_pages_are_unchanged(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    """content_sha skipping is what makes a re-run cheap."""
    _company_with_page(conn, settings)
    llm = FakeLLM(
        CompanyProfile(domain="acme.ch", one_liner="x", offerings=()),
        CompanyProfile(domain="acme.ch", one_liner="x", offerings=()),
    )

    extract.extract(conn, SEGMENT, settings, llm)
    first_calls = llm.calls
    extract.extract(conn, SEGMENT, settings, llm, only_changed=True)

    assert llm.calls == first_calls, "an unchanged company should not be sent to the model again"


def test_extract_reruns_when_not_asked_to_skip(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    _company_with_page(conn, settings)
    llm = FakeLLM(
        CompanyProfile(domain="acme.ch", one_liner="x", offerings=()),
        CompanyProfile(domain="acme.ch", one_liner="y", offerings=()),
    )

    extract.extract(conn, SEGMENT, settings, llm)
    extract.extract(conn, SEGMENT, settings, llm, only_changed=False)
    assert llm.calls == 2


def test_a_model_returning_nothing_is_counted_not_fatal(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    _company_with_page(conn, settings)
    llm = FakeLLM(None)

    report = extract.extract(conn, SEGMENT, settings, llm)

    assert report.failed == 1
    assert report.offerings_kept == 0


def test_extract_accumulates_cost(conn: sqlite3.Connection, settings: Settings) -> None:
    _company_with_page(conn, settings)
    llm = FakeLLM(CompanyProfile(domain="acme.ch", one_liner="x", offerings=()))

    report = extract.extract(conn, SEGMENT, settings, llm)
    assert report.usage.input_tokens == 100
    assert report.usage.cost_usd > 0


def test_extract_is_a_no_op_without_fetched_pages(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    db.upsert_segment(conn, SEGMENT.slug, SEGMENT.name, "slug: test-seg")
    company_id = db.upsert_company(conn, domain="empty.ch", canonical_name="Empty")
    db.upsert_membership(conn, segment_slug=SEGMENT.slug, company_id=company_id)
    conn.commit()

    llm = FakeLLM()
    report = extract.extract(conn, SEGMENT, settings, llm)

    assert llm.calls == 0
    assert report.companies == 0


def test_a_long_but_verbatim_quote_is_accepted() -> None:
    """One over-long quote must not discard the whole profile.

    The schema validates a CompanyProfile as a unit, so a single quote past the
    limit used to fail the entire company and throw away every good offering
    with it. Losing one claim to a rule is reasonable; losing the company is
    not. The substring check, not the length cap, is what keeps claims honest.
    """
    long_quote = PAGE_TEXT[:200]
    assert len(long_quote) > 120
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="x",
        offerings=(Offering(label="Long", evidence_quote=long_quote, evidence_url=PAGE_URL),),
    )
    kept, dropped = extract.drop_unsupported(profile, {PAGE_URL: PAGE_TEXT})
    assert len(kept.offerings) == 1
    assert dropped == 0


def test_a_long_fabricated_quote_is_still_dropped() -> None:
    """Raising the length cap must not weaken the check that actually matters."""
    fabricated = (
        "Acme Intelligence provides a fully managed round-the-clock AI operations "
        "service with a guaranteed 99.99% uptime SLA and dedicated support engineers."
    )
    assert len(fabricated) > 120
    profile = CompanyProfile(
        domain="acme.ch",
        one_liner="x",
        offerings=(Offering(label="Managed", evidence_quote=fabricated, evidence_url=PAGE_URL),),
    )
    kept, dropped = extract.drop_unsupported(profile, {PAGE_URL: PAGE_TEXT})
    assert len(kept.offerings) == 0
    assert dropped == 1
