"""Industry names collapse to one closed vocabulary.

The failure this prevents is not exotic. Left to free text, an extractor
produced `hospitality`, `Hospitality`, `real estate`, `real_estate`,
`Real Estate` and `Immobilien` as six different industries, and a table meant
to show where the market is concentrated became eighty-two rows nobody could
read. Casing, punctuation and language are not distinctions worth a row.
"""

from __future__ import annotations

import pytest

from sectorradar.industries import INDUSTRIES, canonical_industry


@pytest.mark.parametrize(
    "written",
    ["real estate", "real_estate", "Real Estate", "REAL ESTATE", "  Real  Estate  ", "Immobilien"],
)
def test_every_spelling_of_one_sector_lands_on_the_same_value(written: str) -> None:
    assert canonical_industry(written) == "real_estate"


def test_german_and_french_names_map_to_the_english_canonical() -> None:
    assert canonical_industry("Versicherung") == "insurance"
    assert canonical_industry("santé") == "healthcare"
    assert canonical_industry("Medien") == "media"


def test_near_synonyms_are_merged_rather_than_kept_apart() -> None:
    """`manufacturing` and `industrial` are the same column in any real report."""
    assert canonical_industry("manufacturing") == canonical_industry("industrial")
    assert canonical_industry("transport") == canonical_industry("logistics")


def test_a_compound_name_takes_its_leading_sector() -> None:
    assert canonical_industry("Handel & E-Commerce") == "retail"
    assert canonical_industry("Health & Biomedical") == "healthcare"


def test_something_that_is_not_an_industry_is_refused() -> None:
    """`Cross-Industry` is the absence of a sector, and must not become one."""
    assert canonical_industry("Cross-Industry") is None
    assert canonical_industry("various") is None
    assert canonical_industry("") is None
    assert canonical_industry(None) is None


def test_an_unrecognised_sector_is_refused_rather_than_passed_through() -> None:
    """Passing unknowns through is how eighty-two values happened in the first place."""
    assert canonical_industry("interdimensional freight") is None


def test_every_canonical_value_maps_to_itself() -> None:
    for value in INDUSTRIES:
        assert canonical_industry(value) == value


def test_the_vocabulary_stays_small_enough_to_read() -> None:
    """A table of industries is a thing people scan, not scroll."""
    assert len(INDUSTRIES) <= 30


def test_canonical_values_are_lowercase_snake_case() -> None:
    for value in INDUSTRIES:
        assert value == value.lower()
        assert " " not in value
        assert "-" not in value
