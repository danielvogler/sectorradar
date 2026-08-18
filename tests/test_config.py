"""Segment and settings loading.

The behaviour that matters here is the failure mode: an invalid segment YAML
must fail at CLI start with a message naming the offending field, not halfway
through a crawl.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sectorradar.config import (
    ConfigError,
    Segment,
    Settings,
    available_segments,
    load_segment,
    load_settings,
)

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
    segment = load_segment("ai-assurance-ch")
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


# --- facet vocabularies -----------------------------------------------------


def _with_facets(facets: object) -> Segment:
    return Segment.model_validate(
        {
            "slug": "s",
            "name": "Test market, Somewhere",
            "geo": {"country": "CH"},
            "inclusion": "Include companies that do the thing described here.",
            "tiers": {1: "primary"},
            "facets": facets,
        }
    )


def test_a_facet_written_as_a_plain_list_still_works() -> None:
    """The simple form stays valid; keywords are an option, not a tax."""
    segment = _with_facets({"service_type": ["pentest", "forensics"]})

    assert segment.facet_values("service_type") == ["pentest", "forensics"]
    assert segment.facet_keywords("service_type", "pentest") == ()


def test_a_facet_can_carry_the_words_that_ground_each_value() -> None:
    """Market vocabulary belongs in the segment file, not in Python.

    `pentest` never appears on a page offering *Penetrationstests*, and an
    ungrounded tag is dropped — so a segment that cannot supply its own words
    reports an emptier market than it found.
    """
    segment = _with_facets({"service_type": {"pentest": ["penetrationstest", "penetration test"]}})

    assert segment.facet_values("service_type") == ["pentest"]
    assert segment.facet_keywords("service_type", "pentest") == (
        "penetrationstest",
        "penetration test",
    )


def test_an_unknown_facet_or_value_yields_nothing_rather_than_raising() -> None:
    segment = _with_facets({"service_type": ["pentest"]})

    assert segment.facet_values("nonsense") == []
    assert segment.facet_keywords("service_type", "unheard_of") == ()
    assert segment.facet_keywords("nonsense", "pentest") == ()


@pytest.mark.parametrize("slug", ["ai-assurance-ch", "pilates-zurich"])
def test_every_shipped_facet_supplies_its_evidence_words(slug: str) -> None:
    """An example that teaches the pattern has to follow it everywhere.

    `pilates-zurich` declared `service_type` correctly and left
    `delivery_model` and `vertical` as bare lists. Five of its nine values in
    those two facets were never applied and 463 tags were dropped ungrounded —
    on the segment the README tells people to run first.
    """
    segment = load_segment(slug)

    bare = [
        f"{facet}.{value}"
        for facet in segment.facets
        for value in segment.facet_values(facet)
        if not segment.facet_keywords(facet, value)
    ]
    assert not bare, f"declared with no evidence words, so silently droppable: {bare}"


# --- the two fields that become the page ------------------------------------


def _named(name: str, inclusion: str | None = None) -> Segment:
    return Segment.model_validate(
        {
            "slug": "x-seg",
            "name": name,
            "geo": {"country": "CH"},
            "inclusion": inclusion
            or "Include a company if it offers this as a named service on its own website.",
            "tiers": {1: "primary"},
        }
    )


@pytest.mark.parametrize("name", ["ai-assurance-ch", "test-segment"])
def test_a_slug_pasted_into_the_name_is_refused(name: str) -> None:
    """`name` is the page headline and the browser title, not an internal label.

    A slug type-checks and then greets everybody who opens the result.
    """
    with pytest.raises(ValidationError, match="slug"):
        _named(name)


@pytest.mark.parametrize("name", ["Test", "x", "AI"])
def test_a_name_too_short_to_describe_a_market_is_refused(name: str) -> None:
    with pytest.raises(ValidationError, match="describe a market"):
        _named(name)


def test_a_real_market_name_is_accepted_and_trimmed() -> None:
    assert _named("  Pilates studios, Zürich  ").name == "Pilates studios, Zürich"


def test_an_inclusion_rule_that_describes_instead_of_instructing_is_refused() -> None:
    """It is injected verbatim into the classifier prompt.

    "Swiss AI companies" tells a model nothing about where the edge is, and the
    edge is the entire job.
    """
    with pytest.raises(ValidationError, match="does not say what to include"):
        _named("Pilates studios, Zürich", inclusion="Swiss companies doing pilates work.")


def test_the_inclusion_rule_is_normalised_for_the_prompt() -> None:
    """It is shown under the headline as well, so stray wrapping matters."""
    segment = _named(
        "Pilates studios, Zürich",
        inclusion="Include a business   if it\n  offers Pilates instruction to the public.",
    )

    assert "\n" not in segment.inclusion
    assert "   " not in segment.inclusion


@pytest.mark.parametrize("slug", ["ai-assurance-ch", "pilates-zurich"])
def test_every_shipped_segment_would_render_a_readable_header(slug: str) -> None:
    """The name goes straight into an <h1>, so the examples must model it."""
    segment = load_segment(slug)

    assert " " in segment.name
    assert segment.name[0].isupper()
    assert segment.name != slug


def test_the_project_can_come_from_either_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gcloud` sets GOOGLE_CLOUD_PROJECT; this project prefixes its own.

    Reading only the prefixed one meant the pipeline ran happily while
    `make bucket` refused, for the same account and the same `.env`.
    """
    monkeypatch.delenv("SECTORRADAR_GCP_PROJECT", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-gcloud")
    monkeypatch.setenv("SECTORRADAR_CONTACT", "a@b.ch")

    assert load_settings().gcp_project == "from-gcloud"


def test_the_prefixed_project_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-gcloud")
    monkeypatch.setenv("SECTORRADAR_GCP_PROJECT", "explicit")
    monkeypatch.setenv("SECTORRADAR_CONTACT", "a@b.ch")

    assert load_settings().gcp_project == "explicit"


def test_the_bucket_script_reads_the_same_two_variables() -> None:
    """Two places resolving one setting is how they drift apart."""
    script = (Path(__file__).resolve().parent.parent / "scripts" / "bucket.sh").read_text()

    assert "SECTORRADAR_GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT" in script


def test_every_setting_is_actually_read_from_the_environment() -> None:
    """A field on `Settings` that `load_settings` forgets defaults forever.

    Three of them did: `gcs_bucket`, `gcs_location` and `search_model` were
    declared, documented in .env.example, and never read — so the bucket sat in
    .env while the CLI insisted no bucket was configured. The shell script
    sourced .env directly and worked, which made it look like a Python bug
    rather than a missing line.
    """
    source = (
        Path(__file__).resolve().parent.parent / "src" / "sectorradar" / "config.py"
    ).read_text(encoding="utf-8")
    wiring = source[source.index("def load_settings") :]

    # Fields the loader computes rather than reads, with why.
    derived = {"log_level"}

    missing = [
        name for name in Settings.model_fields if name not in derived and f"{name}=" not in wiring
    ]
    assert not missing, f"declared on Settings but never read from the environment: {missing}"
