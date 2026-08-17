"""The pydantic contracts that cross module boundaries."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sectorradar.models import (
    MAX_EVIDENCE_CHARS,
    Candidate,
    Classification,
    CompanyProfile,
    GeoPoint,
    Offering,
)

QUOTE = "We build LLM agents for enterprise clients."
URL = "https://example.ch/services"


def _offering(label: str = "Agent development", quote: str = QUOTE, url: str = URL) -> Offering:
    return Offering(label=label, evidence_quote=quote, evidence_url=url)


def test_offering_round_trips() -> None:
    offering = _offering()
    assert offering.label == "Agent development"
    assert offering.evidence_url.endswith("/services")


def test_offering_is_immutable() -> None:
    """Value objects are frozen so a later stage cannot quietly rewrite evidence."""
    offering = _offering()
    with pytest.raises(ValidationError):
        offering.label = "something else"


def test_evidence_quote_is_length_capped() -> None:
    """The cap stops a model smuggling a paraphrased paragraph past the substring check."""
    with pytest.raises(ValidationError, match="evidence_quote"):
        _offering(quote="x" * (MAX_EVIDENCE_CHARS + 1))


def test_offering_rejects_an_empty_quote() -> None:
    with pytest.raises(ValidationError):
        _offering(quote="")


def test_offering_rejects_unknown_fields() -> None:
    """extra='forbid' means a renamed field fails loudly instead of vanishing."""
    with pytest.raises(ValidationError):
        # model_validate, not the constructor: extra="forbid" exists to guard
        # data arriving from outside the process, which is how it is really hit.
        Offering.model_validate(
            {"label": "x", "evidence_quote": QUOTE, "evidence_url": URL, "sneaky": "value"}
        )


def test_company_profile_defaults_are_empty_not_none() -> None:
    profile = CompanyProfile(domain="example.ch")
    assert profile.offerings == ()
    assert profile.languages == ()
    assert profile.facets == {}
    assert profile.headcount_estimate is None


def test_company_profile_rejects_an_absurd_founding_year() -> None:
    with pytest.raises(ValidationError):
        CompanyProfile(domain="example.ch", founded_year=1200)


def test_company_profile_rejects_a_negative_headcount() -> None:
    with pytest.raises(ValidationError):
        CompanyProfile(domain="example.ch", headcount_estimate=0)


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        CompanyProfile(domain="example.ch", confidence=1.5)


def test_classification_requires_a_rationale() -> None:
    """A tier without a written reason is the failure mode this project exists to avoid."""
    with pytest.raises(ValidationError):
        Classification(tier=1, tier_rationale="", relevance=0.9)


def test_classification_accepts_an_undecided_tier() -> None:
    result = Classification(tier=None, tier_rationale="Not enough evidence", relevance=0.1)
    assert result.tier is None


def test_classification_rejects_a_tier_outside_the_scale() -> None:
    with pytest.raises(ValidationError):
        Classification(tier=7, tier_rationale="nope", relevance=0.5)


def test_candidate_treats_blank_strings_as_missing() -> None:
    """Sources emit "" as often as null; downstream should see one of them."""
    candidate = Candidate(segment_slug="seg", source="websearch", raw_name="  ", raw_url="")
    assert candidate.raw_name is None
    assert candidate.raw_url is None


def test_candidate_stamps_a_discovery_time() -> None:
    candidate = Candidate(segment_slug="seg", source="seeds")
    assert candidate.discovered_at.tzinfo is not None


def test_candidate_requires_provenance() -> None:
    with pytest.raises(ValidationError):
        Candidate(segment_slug="seg")  # type: ignore[call-arg]


def test_geopoint_rejects_out_of_range_coordinates() -> None:
    with pytest.raises(ValidationError):
        GeoPoint(lat=91.0, lon=8.5, provider="swisstopo")


def test_geopoint_accepts_a_swiss_location() -> None:
    point = GeoPoint(lat=47.3769, lon=8.5417, provider="swisstopo")
    assert point.provider == "swisstopo"
