"""Pydantic contracts that cross a module boundary.

Anything arriving from outside the process — an LLM response, a search API, a
SPARQL result — is parsed into one of these at the module edge. Nothing further
in should ever see a bare ``dict[str, Any]``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ReviewState = Literal["pending", "accepted", "rejected", "needs_info"]
Tier = Literal[1, 2, 3, 4]

# An evidence quote is a verbatim fragment, capped so that the model cannot
# smuggle a whole paraphrased paragraph past the substring check.
MAX_EVIDENCE_CHARS = 120


class Frozen(BaseModel):
    """Base for value objects. Immutable by default, per the house style."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Offering(Frozen):
    """One named service a company sells, with the sentence that proves it."""

    label: str = Field(min_length=1, max_length=120)
    evidence_quote: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARS)
    evidence_url: str = Field(min_length=1)


class CompanyProfile(Frozen):
    """The structured profile an LLM extracts from a company's own website."""

    domain: str
    one_liner: str = Field(default="", max_length=400)
    legal_name: str | None = None
    offerings: tuple[Offering, ...] = ()
    facets: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    headcount_estimate: int | None = Field(default=None, ge=1, le=1_000_000)
    founded_year: int | None = Field(default=None, ge=1800, le=2100)
    languages: tuple[str, ...] = ()
    city: str | None = None
    canton: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Classification(Frozen):
    """Where a company sits relative to one segment's inclusion rule."""

    tier: Tier | None = None
    tier_rationale: str = Field(min_length=1, max_length=1000)
    relevance: float = Field(ge=0.0, le=1.0)
    facets: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class Candidate(Frozen):
    """A company-shaped thing a discovery source found, before resolution.

    Deliberately permissive: sources emit noise, and it is ``resolve.py``'s job
    to decide what is real. Only the provenance fields are required.
    """

    segment_slug: str = Field(min_length=1)
    source: str = Field(min_length=1)
    raw_name: str | None = None
    raw_url: str | None = None
    source_detail: str | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("raw_name", "raw_url", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        """Sources emit "" as often as they emit null; treat them alike."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class GeoPoint(Frozen):
    """A geocoded location and the service that produced it."""

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    provider: str
    matched_address: str | None = None


__all__ = [
    "MAX_EVIDENCE_CHARS",
    "Candidate",
    "Classification",
    "CompanyProfile",
    "Frozen",
    "GeoPoint",
    "Offering",
    "ReviewState",
    "Tier",
]
