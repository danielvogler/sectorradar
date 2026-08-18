"""How much a company can demonstrate in public.

Read the name carefully: this scores *visible* traction, not success. Nothing
here can see revenue, margin, or whether a firm is about to fold. What it can
see is how much verifiable evidence of delivery a company puts on the record —
projects, named clients, products, coverage somebody else wrote, a team, a
hiring page, years on the clock.

Two design decisions carry most of the honesty:

* **Silence is reported as silence.** A company with nothing published scores
  zero *and* is flagged unknown, with a confidence of nought. A profitable
  firm that never writes case studies looks identical to an empty shell from
  the outside, and the score must not pretend otherwise. Treat the number as a
  floor on what a company can prove, never as a ceiling on what it is.
* **Self-published coverage is not coverage.** A press release on a company's
  own site is an assertion; an article somebody else wrote is evidence. Only
  the second scores. It is the one component a company cannot manufacture by
  editing its own website, which is exactly what makes it worth the most.

Each component is capped, so no single loud signal can carry a company up the
ranking on its own, and every component is reported alongside the total so the
number can always be taken apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

#: Weights sum to 100. They are a judgement call, not a measurement, which is
#: why the breakdown travels with the score everywhere it is displayed.
WEIGHTS: Final[dict[str, float]] = {
    "delivery": 20.0,
    "clients": 18.0,
    "coverage": 20.0,
    "product": 10.0,
    "scale": 15.0,
    "longevity": 9.0,
    "hiring": 8.0,
}

#: Where each component stops paying. Beyond these, more of the same tells you
#: nothing new: the tenth case study does not distinguish a firm the way the
#: first three do.
CASE_STUDY_CAP: Final = 8
CLIENT_CAP: Final = 10
COVERAGE_CAP: Final = 5
PRODUCT_CAP: Final = 3
HEADCOUNT_CAP: Final = 250
AGE_CAP_YEARS: Final = 15


@dataclass(frozen=True)
class Inputs:
    """Everything the score is allowed to look at, all of it evidence-backed."""

    case_studies: int
    named_clients: int
    products: int
    independent_mentions: int
    self_published_mentions: int
    headcount: int | None
    founded_year: int | None
    is_hiring: bool


@dataclass(frozen=True)
class Component:
    """One contribution to the total, kept separable so it can be argued with."""

    name: str
    points: float
    max_points: float
    detail: str
    observed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "points": round(self.points, 1),
            "max_points": self.max_points,
            "detail": self.detail,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class Traction:
    points: int
    components: tuple[Component, ...]
    confidence: float

    @property
    def is_unknown(self) -> bool:
        """True when nothing at all could be observed about this company.

        Distinct from a low score, and the distinction matters: one says "we
        looked and found little", the other says "we found nothing to look at".
        """
        return self.confidence == 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": self.points,
            "confidence": round(self.confidence, 2),
            "is_unknown": self.is_unknown,
            "components": [c.to_dict() for c in self.components],
        }


def _linear(count: int, cap: int, weight: float) -> float:
    return min(count, cap) / cap * weight


def _by_magnitude(headcount: int, cap: int, weight: float) -> float:
    """Score team size on a log scale.

    The step from three people to thirty is a change of kind; the step from
    three hundred to three hundred and thirty is noise. Linear scaling would
    let a handful of large firms flatten every distinction below them.
    """
    if headcount <= 1:
        return 0.0
    return min(1.0, math.log10(headcount) / math.log10(cap)) * weight


def score(inputs: Inputs, this_year: int) -> Traction:
    """Turn observed evidence into a 0-100 score with its reasoning attached."""
    coverage_detail = f"{inputs.independent_mentions} independent"
    if inputs.self_published_mentions:
        coverage_detail += f", {inputs.self_published_mentions} self-published (not scored)"

    components: list[Component] = [
        Component(
            "delivery",
            _linear(inputs.case_studies, CASE_STUDY_CAP, WEIGHTS["delivery"]),
            WEIGHTS["delivery"],
            f"{inputs.case_studies} reference projects",
            inputs.case_studies > 0,
        ),
        Component(
            "clients",
            _linear(inputs.named_clients, CLIENT_CAP, WEIGHTS["clients"]),
            WEIGHTS["clients"],
            f"{inputs.named_clients} named clients",
            inputs.named_clients > 0,
        ),
        Component(
            "coverage",
            _linear(inputs.independent_mentions, COVERAGE_CAP, WEIGHTS["coverage"]),
            WEIGHTS["coverage"],
            coverage_detail,
            inputs.independent_mentions > 0,
        ),
        Component(
            "product",
            _linear(inputs.products, PRODUCT_CAP, WEIGHTS["product"]),
            WEIGHTS["product"],
            f"{inputs.products} named products",
            inputs.products > 0,
        ),
    ]

    if inputs.headcount is None:
        components.append(
            Component("scale", 0.0, WEIGHTS["scale"], "team size not published", False)
        )
    else:
        components.append(
            Component(
                "scale",
                _by_magnitude(inputs.headcount, HEADCOUNT_CAP, WEIGHTS["scale"]),
                WEIGHTS["scale"],
                f"about {inputs.headcount} people",
                True,
            )
        )

    # A founding year after today is a registry or extraction error, not a
    # company from the future. Score it as unobserved rather than letting a
    # negative age subtract points from an otherwise sound profile.
    age = this_year - inputs.founded_year if inputs.founded_year is not None else None
    if age is None or age < 0:
        components.append(
            Component("longevity", 0.0, WEIGHTS["longevity"], "founding year unknown", False)
        )
    else:
        components.append(
            Component(
                "longevity",
                _linear(age, AGE_CAP_YEARS, WEIGHTS["longevity"]),
                WEIGHTS["longevity"],
                f"{age} years old",
                True,
            )
        )

    components.append(
        Component(
            "hiring",
            WEIGHTS["hiring"] if inputs.is_hiring else 0.0,
            WEIGHTS["hiring"],
            "advertises open roles" if inputs.is_hiring else "no careers page found",
            inputs.is_hiring,
        )
    )

    observed = sum(1 for c in components if c.observed)
    return Traction(
        points=round(sum(c.points for c in components)),
        components=tuple(components),
        confidence=observed / len(components),
    )
