"""Canton normalisation.

Every value below was either observed in a real extraction run or is a spelling
a Swiss company uses on its own site. The two behaviours that matter: fold
every real spelling to one code, and refuse anything that is not a canton —
because a wrong canton is worse than a missing one. It is unfilterable *and* it
looks correct.
"""

from __future__ import annotations

import pytest

from sectorradar import swiss


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ZH", "ZH"),
        ("zh", "ZH"),
        ("Zürich", "ZH"),
        ("Zurich", "ZH"),
        ("Zuerich", "ZH"),
        ("zurigo", "ZH"),
        ("Kanton Zürich", "ZH"),
        ("Canton of Zurich", "ZH"),
        ("Genève", "GE"),
        ("Geneva", "GE"),
        ("Genf", "GE"),
        ("canton de Genève", "GE"),
        ("Ticino", "TI"),
        ("Tessin", "TI"),
        ("Graubünden", "GR"),
        ("Grisons", "GR"),
        ("Grigioni", "GR"),
        ("St. Gallen", "SG"),
        ("St Gallen", "SG"),
        ("Sankt Gallen", "SG"),
        ("Saint Gall", "SG"),
        ("Basel-Stadt", "BS"),
        ("Basel", "BS"),
        ("Baselland", "BL"),
        ("Vaud", "VD"),
        ("Waadt", "VD"),
        ("Wallis", "VS"),
        ("Valais", "VS"),
        ("Neuchâtel", "NE"),
        ("Neuenburg", "NE"),
        ("Zug", "ZG"),
        ("Luzern", "LU"),
        ("Lucerne", "LU"),
        ("Bern", "BE"),
        ("Berne", "BE"),
    ],
)
def test_every_real_spelling_folds_to_one_code(value: str, expected: str) -> None:
    assert swiss.canton_code(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        # All four observed in a single real extraction run.
        "Alberta",
        "Hesse",
        "Switzerland",
        "Zentralschweiz",
        # Plausible but not cantons.
        "Suisse romande",
        "DACH",
        "Europe",
        "XX",
        "ZZ",
        "",
        "   ",
        None,
    ],
)
def test_anything_that_is_not_a_canton_is_refused(value: str | None) -> None:
    assert swiss.canton_code(value) is None
    assert not swiss.is_canton(value)


def test_the_variants_that_broke_the_ui_all_meet() -> None:
    """These three appeared as separate entries in the canton filter."""
    assert swiss.canton_code("ZH") == swiss.canton_code("Zurich") == swiss.canton_code("Zürich")


def test_all_26_cantons_are_present() -> None:
    assert len(swiss.CANTONS) == 26


def test_every_code_round_trips_through_its_name() -> None:
    for code, name in swiss.CANTONS.items():
        assert swiss.canton_code(name) == code, f"{name} should fold back to {code}"


def test_canton_name_gives_something_geocodable() -> None:
    assert swiss.canton_name("TI") == "Ticino"
    assert swiss.canton_name("zh") == "Zürich"


def test_canton_name_refuses_a_non_code() -> None:
    assert swiss.canton_name("Zurich") is None
    assert swiss.canton_name(None) is None


# --- city exonyms -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Eight companies recorded in "Geneva" were excluded from the segment
        # as foreign, because the geocoder answers to "Genève".
        ("Geneva", "Genève"),
        ("Genf", "Genève"),
        ("Ginevra", "Genève"),
        ("Zurich", "Zürich"),
        ("Zuerich", "Zürich"),
        ("Lucerne", "Luzern"),
        ("Berne", "Bern"),
        ("Basle", "Basel"),
        ("Saint Gall", "St. Gallen"),
        ("Neuchatel", "Neuchâtel"),
        ("Coire", "Chur"),
    ],
)
def test_city_exonyms_fold_to_the_local_spelling(value: str, expected: str) -> None:
    assert swiss.canonical_city(value) == expected


def test_an_unknown_town_is_left_alone() -> None:
    """Most Swiss towns have one name and are already spelled correctly."""
    assert swiss.canonical_city("Wädenswil") == "Wädenswil"
    assert swiss.canonical_city("Rebstein") == "Rebstein"


def test_city_whitespace_is_collapsed() -> None:
    assert swiss.canonical_city("  Zug   ") == "Zug"


def test_no_city_is_none() -> None:
    assert swiss.canonical_city(None) is None
    assert swiss.canonical_city("  ") is None
