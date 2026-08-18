"""Pydantic contracts that cross a module boundary.

Anything arriving from outside the process — an LLM response, a search API, a
SPARQL result — is parsed into one of these at the module edge. Nothing further
in should ever see a bare ``dict[str, Any]``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ReviewState = Literal["pending", "accepted", "rejected", "needs_info"]
Tier = Literal[1, 2, 3, 4]

# An evidence quote is a verbatim fragment. The cap is a secondary guard
# against a whole paraphrased paragraph being passed off as a quote; the real
# defence is the substring check in extract.py, which no amount of length gets
# a fabricated claim past.
#
# It sits at 400 rather than the 120 the prompt asks for, because the two
# limits do different jobs. The prompt asks for at most 15 words and models
# mostly comply — but "mostly" matters here: pydantic validates the whole
# CompanyProfile at once, so a single over-long quote used to fail the entire
# profile and discard every good offering alongside it. Losing one claim to a
# rule is reasonable; losing the company is not.
MAX_EVIDENCE_CHARS = 400

#: What the prompt asks for. Quotes longer than this are accepted if they are
#: genuinely verbatim, but their length is a hint that the model is
#: summarising rather than citing.
PREFERRED_EVIDENCE_CHARS = 120


class Frozen(BaseModel):
    """Base for value objects. Immutable by default, per the house style."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Offering(Frozen):
    """One named service a company sells, with the sentence that proves it."""

    label: str = Field(min_length=1, max_length=120)
    evidence_quote: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARS)
    evidence_url: str = Field(min_length=1)


#: Characteristics of a website, as distinct from claims made on it. Whether a
#: site publishes prices or named references is a fact about the business, and
#: it varies sharply with size, which makes it comparable across a market.
#: Every one of these has to answer "what does this tell me about how they
#: sell?". Which languages a site is available in does not — it is a fact about
#: the site, not about the offering, and it sat in this list producing rows
#: nobody could act on. Language coverage stays on the company record, where it
#: belongs, rather than in a comparison of how firms go to market.
SITE_SIGNALS: tuple[str, ...] = (
    # Evidence of delivery
    "named_clients",
    "case_studies",
    "quantified_outcomes",
    "industry_pages",
    # Commercial posture
    "pricing_published",
    "free_assessment",
    "demo_or_trial",
    "methodology_described",
    # Credibility
    "certifications",
    "partner_badges",
    "open_source",
    "events_or_talks",
    # Scale
    "team_page",
    "careers_page",
    "blog",
)


class CaseStudy(Frozen):
    """A project a company says it has delivered.

    The most informative thing on most consultancy websites, and the least
    structured. "Who has actually built this, and for what kind of client" is
    a different question from "who says they offer it", and only this answers
    it.
    """

    title: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=60)
    summary: str = Field(default="", max_length=600)
    evidence_quote: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARS)
    evidence_url: str = Field(min_length=1)


class ClientReference(Frozen):
    """A client a company names on its own site.

    ``relationship`` is carried rather than flattened because a named project
    client, a logo on a wall and a quoted testimonial are different strengths
    of evidence, and a reference is only worth what its evidence is worth.
    """

    client_name: str = Field(min_length=2, max_length=120)
    industry: str | None = Field(default=None, max_length=60)
    relationship: str = Field(default="mentioned", max_length=40)
    evidence_quote: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARS)
    evidence_url: str = Field(min_length=1)


class Product(Frozen):
    """A named product or platform, as distinct from a service.

    A consultancy that also sells a product is a different business from one
    that does not, and the two are indistinguishable in a list of offerings.
    """

    name: str = Field(min_length=2, max_length=120)
    kind: str = Field(default="product", max_length=40)
    summary: str = Field(default="", max_length=400)
    evidence_quote: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARS)
    evidence_url: str = Field(min_length=1)


#: What a mention *is*. An award and a funding round are different news about
#: a company, and flattening them into "press" throws away the distinction that
#: makes either worth reading.
MENTION_KINDS: Final[tuple[str, ...]] = (
    "news",
    "award",
    "funding",
    "partnership",
    "talk",
    "press_release",
)


class MediaMention(Frozen):
    """Coverage a company has received, or claims to have received.

    ``is_self_published`` is the load-bearing field. A page on a company's own
    site headed "press" is an assertion about coverage; an article on a news
    site is the coverage. Both are worth recording — a firm that lists twelve
    press appearances is telling you something — but only the second is
    evidence somebody other than the company thought it was worth writing
    about, and the two must never be added together.
    """

    headline: str = Field(min_length=3, max_length=250)
    #: Publication or conference. None when the site names no outlet.
    outlet: str | None = Field(default=None, max_length=120)
    kind: str = Field(default="news", max_length=30)
    published_year: int | None = Field(default=None, ge=1990, le=2100)
    #: Where the coverage itself lives — often a different host from the site.
    url: str = Field(min_length=1)
    is_self_published: bool = True
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
    # The postal address, as specifically as the site gives it. Without a
    # street every company in a city geocodes to the same point — 49 firms
    # landed on one coordinate in Zürich — and no amount of zooming separates
    # markers that are genuinely identical.
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    canton: str | None = None

    # Depth. These are what make one company comparable with another rather
    # than merely listed beside it.
    case_studies: tuple[CaseStudy, ...] = ()
    named_clients: tuple[ClientReference, ...] = ()
    products: tuple[Product, ...] = ()
    #: Press, awards and coverage the site points to.
    media_mentions: tuple[MediaMention, ...] = ()
    #: Client industries the site names, as free text; normalised downstream.
    industries_served: tuple[str, ...] = ()
    #: Formats a training offering comes in — half-day, in-house, certificate.
    workshop_formats: tuple[str, ...] = ()
    #: Technologies and platforms the site names as things it works with.
    technologies: tuple[str, ...] = ()
    #: Where the thing runs. A firm that will deploy on a client's own
    #: hardware is selling to a different buyer from one that only ships to a
    #: hyperscaler, and in a market with bank and hospital clients that is
    #: often the deciding question rather than a detail.
    hosting: tuple[str, ...] = ()
    #: Named cloud platforms, kept apart from `technologies` because "which
    #: hyperscaler" is a procurement constraint and "which agent framework" is
    #: an engineering preference.
    cloud_providers: tuple[str, ...] = ()
    #: Partner or certification badges the site displays.
    certifications: tuple[str, ...] = ()
    #: How the company positions itself, in its own words.
    positioning: str | None = Field(default=None, max_length=400)
    #: Site characteristics, keyed by :data:`SITE_SIGNALS`.
    site_signals: dict[str, bool] = Field(default_factory=dict)

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Classification(Frozen):
    """Where a company sits relative to one segment's inclusion rule."""

    # A bounded int rather than ``Tier`` (a Literal), because this model is
    # handed to a provider as a response schema and Gemini's schema dialect
    # requires enum members to be strings — an int Literal is rejected outright
    # and every classification call fails. ge/le enforces the same 1-4 range.
    tier: int | None = Field(default=None, ge=1, le=4)
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
    # Location a source already knows. A hand-curated seed list carries it; most
    # other sources do not, and it is filled in later from the company's site.
    raw_city: str | None = None
    raw_canton: str | None = None

    @field_validator("raw_name", "raw_url", "raw_city", "raw_canton", mode="before")
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
    "SITE_SIGNALS",
    "Candidate",
    "CaseStudy",
    "Classification",
    "ClientReference",
    "CompanyProfile",
    "Frozen",
    "GeoPoint",
    "Offering",
    "Product",
    "ReviewState",
    "Tier",
]
