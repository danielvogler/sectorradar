"""Segment and settings loading.

The behaviour that matters here is the failure mode: an invalid segment YAML
must fail at CLI start with a message naming the offending field, not halfway
through a crawl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sectorradar.config import ConfigError, Settings, available_segments, load_segment

VALID = """
slug: test-seg
name: A test segment
geo:
  country: CH
inclusion: >
  Include a company if it sells widgets as a named service on its own website.
tiers:
  1: "Widgets are the primary offering"
  2: "Sells widgets among other things"
enrich_tiers: [1, 2]
facets:
  service_type: [widgets, gadgets]
sources:
  seeds:
    enabled: true
    urls: ["https://example.ch"]
  websearch:
    enabled: false
    queries: ["widgets switzerland"]
gold_set:
  - domain: known-competitor.ch
    expected_tier: 1
"""


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_a_valid_segment(segments_dir: Path) -> None:
    _write(segments_dir, "test-seg", VALID)
    segment = load_segment("test-seg", base=segments_dir)

    assert segment.slug == "test-seg"
    assert segment.geo.country == "CH"
    assert segment.enrich_tiers == [1, 2]
    assert segment.enabled_sources() == ["seeds"]
    assert segment.gold_domains() == {"known-competitor.ch"}


def test_source_config_keeps_arbitrary_keys(segments_dir: Path) -> None:
    """Sources read their own keys; config.py must not have to know them all."""
    _write(segments_dir, "test-seg", VALID)
    segment = load_segment("test-seg", base=segments_dir)
    seeds = segment.source("seeds")
    assert seeds.enabled
    assert getattr(seeds, "urls", None) == ["https://example.ch"]


def test_unknown_source_defaults_to_disabled(segments_dir: Path) -> None:
    _write(segments_dir, "test-seg", VALID)
    segment = load_segment("test-seg", base=segments_dir)
    assert segment.source("lindas").enabled is False


def test_missing_segment_lists_what_is_available(segments_dir: Path) -> None:
    _write(segments_dir, "test-seg", VALID)
    with pytest.raises(ConfigError, match="available segments: test-seg"):
        load_segment("nope", base=segments_dir)


def test_malformed_yaml_names_the_file(segments_dir: Path) -> None:
    _write(segments_dir, "broken", "slug: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_segment("broken", base=segments_dir)


def test_missing_required_field_names_the_field(segments_dir: Path) -> None:
    _write(segments_dir, "partial", "slug: partial\nname: No geo here\n")
    with pytest.raises(ConfigError) as excinfo:
        load_segment("partial", base=segments_dir)
    message = str(excinfo.value)
    assert "geo" in message
    assert "inclusion" in message
    assert "tiers" in message


def test_unknown_top_level_key_is_rejected(segments_dir: Path) -> None:
    """A typo in a key must fail loudly rather than being silently ignored."""
    _write(segments_dir, "typo", VALID + "\nenrich_teirs: [1]\n")
    with pytest.raises(ConfigError, match="enrich_teirs"):
        load_segment("typo", base=segments_dir)


def test_bad_tier_number_names_the_problem(segments_dir: Path) -> None:
    _write(segments_dir, "badtier", VALID.replace('  2: "Sells', '  7: "Sells'))
    with pytest.raises(ConfigError, match="tiers must be numbered 1-4"):
        load_segment("badtier", base=segments_dir)


def test_slug_must_match_the_filename(segments_dir: Path) -> None:
    _write(segments_dir, "other-name", VALID)
    with pytest.raises(ConfigError, match="declares slug 'test-seg'"):
        load_segment("other-name", base=segments_dir)


def test_slug_must_be_kebab_case(segments_dir: Path) -> None:
    _write(segments_dir, "Bad_Slug", VALID.replace("slug: test-seg", "slug: Bad_Slug"))
    with pytest.raises(ConfigError, match="slug"):
        load_segment("Bad_Slug", base=segments_dir)


def test_available_segments_is_sorted(segments_dir: Path) -> None:
    _write(segments_dir, "zeta", VALID.replace("test-seg", "zeta"))
    _write(segments_dir, "alpha", VALID.replace("test-seg", "alpha"))
    assert available_segments(base=segments_dir) == ["alpha", "zeta"]


def test_available_segments_on_a_missing_directory(tmp_path: Path) -> None:
    assert available_segments(base=tmp_path / "nothing") == []


def test_the_real_segment_file_is_valid() -> None:
    """The shipped segment must load, or nothing downstream works."""
    segment = load_segment("agentic-ai-ch")
    assert segment.geo.country == "CH"
    assert 1 in segment.tiers
    assert segment.enrich_tiers == [1, 2]


# --- Settings ---------------------------------------------------------------


def test_user_agent_carries_the_contact_address() -> None:
    settings = Settings(contact="me@example.ch")
    agent = settings.user_agent()
    assert "me@example.ch" in agent
    assert "sectorradar" in agent


def test_user_agent_refuses_to_be_anonymous() -> None:
    """Without a contact address the right behaviour is to not crawl at all."""
    settings = Settings(contact=None)
    with pytest.raises(ConfigError, match="SECTORRADAR_CONTACT"):
        settings.user_agent()


def test_vertex_credentials_need_a_project() -> None:
    assert Settings(llm_provider="vertex", gcp_project="p").has_llm_credentials()
    assert not Settings(llm_provider="vertex", gcp_project=None).has_llm_credentials()


def test_derived_paths_sit_next_to_the_database() -> None:
    settings = Settings(db_path=Path("var/radar.db"))
    assert settings.raw_dir == Path("var/raw")
    assert settings.cache_dir == Path("var/cache")
    assert settings.export_dir == Path("var/exports")
