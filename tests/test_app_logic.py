"""Certainty ranking — the thing that makes reviewing all of them optional."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="app extras not installed")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from lib import filters


def _row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {"tier": 1, "relevance": 0.9, "review_state": "pending"}
    return {**base, **over}


def test_a_strong_tier_one_scores_high() -> None:
    score = filters.certainty(_row(), evidence_count=8)
    assert score > 0.75
    assert filters.certainty_label(score) == "high"


def test_an_unclassified_company_with_no_evidence_scores_doubtful() -> None:
    """The tail of the list should be visibly the tail."""
    score = filters.certainty(_row(tier=None, relevance=None), evidence_count=0)
    assert filters.certainty_label(score) == "doubtful"


def test_tier_one_outranks_tier_two_at_equal_relevance() -> None:
    assert filters.certainty(_row(tier=1), 3) > filters.certainty(_row(tier=2), 3)


def test_tier_three_is_ranked_below_tier_two() -> None:
    assert filters.certainty(_row(tier=3), 3) < filters.certainty(_row(tier=2), 3)


def test_evidence_raises_certainty() -> None:
    assert filters.certainty(_row(), 6) > filters.certainty(_row(), 0)


def test_evidence_saturates() -> None:
    """The gap between 0 and 3 matters; the gap between 8 and 40 does not."""
    assert filters.certainty(_row(), 8) == filters.certainty(_row(), 40)


def test_a_human_acceptance_overrides_everything() -> None:
    weak = _row(tier=None, relevance=0.0, review_state="accepted")
    assert filters.certainty(weak, 0) == 1.0


def test_a_human_rejection_sinks_to_zero() -> None:
    strong = _row(tier=1, relevance=1.0, review_state="rejected")
    assert filters.certainty(strong, 20) == 0.0


def test_certainty_is_always_in_range() -> None:
    for tier in (None, 1, 2, 3, 4):
        for relevance in (None, 0.0, 0.5, 1.0):
            for evidence in (0, 1, 50):
                score = filters.certainty(_row(tier=tier, relevance=relevance), evidence)
                assert 0.0 <= score <= 1.0


def test_missing_relevance_does_not_crash() -> None:
    """Unclassified companies have no relevance at all."""
    assert filters.certainty({"tier": None}, 0) >= 0.0


@pytest.mark.parametrize(
    ("score", "label"),
    [(1.0, "high"), (0.75, "high"), (0.6, "medium"), (0.35, "low"), (0.1, "doubtful")],
)
def test_labels_partition_the_range(score: float, label: str) -> None:
    assert filters.certainty_label(score) == label
