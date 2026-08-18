"""How a company's visible traction is scored.

Two properties matter more than the exact weights, and both are easy to get
wrong in a way that produces a plausible-looking number nobody should trust:

* a company with no published evidence must be reported as *unknown*, not as
  bad — the score is a floor on what a firm can demonstrate, and a quiet
  company is not a failing one;
* coverage a company wrote about itself must not count as coverage somebody
  else gave it, because that is the one component of the score the company
  cannot simply assert.
"""

from __future__ import annotations

import pytest

from sectorradar.traction import Inputs, score


def _inputs(**kwargs: object) -> Inputs:
    base: dict[str, object] = {
        "case_studies": 0,
        "named_clients": 0,
        "products": 0,
        "independent_mentions": 0,
        "self_published_mentions": 0,
        "headcount": None,
        "founded_year": None,
        "is_hiring": False,
    }
    base.update(kwargs)
    return Inputs(**base)  # type: ignore[arg-type]


def test_a_company_with_nothing_published_scores_zero_and_is_unknown() -> None:
    result = score(_inputs(), this_year=2026)

    assert result.points == 0
    assert result.confidence == 0.0
    assert result.is_unknown


def test_a_company_with_evidence_is_not_unknown() -> None:
    result = score(_inputs(case_studies=3), this_year=2026)

    assert result.points > 0
    assert not result.is_unknown
    assert result.confidence > 0


def test_self_published_coverage_does_not_count_as_independent() -> None:
    own_horn = score(_inputs(self_published_mentions=20), this_year=2026)
    somebody_else = score(_inputs(independent_mentions=5), this_year=2026)

    assert own_horn.points < somebody_else.points


def test_more_evidence_never_lowers_the_score() -> None:
    fewer = score(_inputs(case_studies=2, named_clients=1), this_year=2026)
    more = score(_inputs(case_studies=6, named_clients=8), this_year=2026)

    assert more.points > fewer.points


def test_each_component_is_capped_so_one_signal_cannot_dominate() -> None:
    result = score(_inputs(case_studies=500), this_year=2026)
    cases = next(c for c in result.components if c.name == "delivery")

    assert cases.points == cases.max_points
    assert result.points < 100


def test_the_score_never_leaves_the_zero_to_hundred_range() -> None:
    everything = score(
        _inputs(
            case_studies=99,
            named_clients=99,
            products=99,
            independent_mentions=99,
            headcount=5000,
            founded_year=1990,
            is_hiring=True,
        ),
        this_year=2026,
    )

    assert everything.points == 100


def test_a_founding_year_in_the_future_is_ignored_rather_than_scored() -> None:
    """A registry typo must not hand out negative or wild longevity points."""
    result = score(_inputs(founded_year=2031), this_year=2026)
    longevity = next(c for c in result.components if c.name == "longevity")

    assert longevity.points == 0


def test_confidence_reflects_how_much_could_actually_be_observed() -> None:
    thin = score(_inputs(case_studies=1), this_year=2026)
    thick = score(
        _inputs(case_studies=1, named_clients=1, headcount=10, founded_year=2015),
        this_year=2026,
    )

    assert thick.confidence > thin.confidence


@pytest.mark.parametrize("headcount", [1, 9, 50, 400])
def test_bigger_teams_score_at_least_as_high_on_scale(headcount: int) -> None:
    smaller = score(_inputs(headcount=1), this_year=2026)
    result = score(_inputs(headcount=headcount), this_year=2026)

    assert result.points >= smaller.points


def test_components_sum_to_the_reported_total() -> None:
    result = score(
        _inputs(case_studies=4, named_clients=3, independent_mentions=2, headcount=20),
        this_year=2026,
    )

    assert round(sum(c.points for c in result.components)) == result.points
