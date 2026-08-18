"""Choosing a model provider, and failing usefully when one is misconfigured.

The pipeline talks to a model through one small protocol, so swapping Vertex
for Claude or GPT is a configuration change rather than a code change. What is
worth testing without a network is the part people actually hit: a typo in
`.env`, a missing key, and the message that comes back.
"""

from __future__ import annotations

import pytest

from sectorradar import llm
from sectorradar.config import ConfigError, Settings

# --- provider selection ------------------------------------------------------


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {"contact": "a@b.ch"}
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_an_unknown_provider_lists_the_ones_that_exist() -> None:
    """The error has to be actionable: a typo here is the likeliest cause."""
    with pytest.raises(ConfigError, match="vertex, anthropic, openai"):
        llm.get_client(_settings(llm_provider="gpt5-turbo-ultra"))


def test_vertex_without_a_project_names_the_command_that_fixes_it() -> None:
    with pytest.raises(ConfigError, match="application-default login"):
        llm.get_client(_settings(llm_provider="vertex", gcp_project=None))


def test_anthropic_without_a_key_says_which_key() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        llm.get_client(_settings(llm_provider="anthropic", anthropic_api_key=None))


def test_openai_without_a_key_says_which_key() -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        llm.get_client(_settings(llm_provider="openai", openai_api_key=None))


def test_the_provider_name_is_matched_case_insensitively() -> None:
    """Nobody should lose ten minutes to `Vertex` in a .env file."""
    with pytest.raises(ConfigError, match="application-default login"):
        llm.get_client(_settings(llm_provider="VERTEX", gcp_project=None))


def test_the_search_model_defaults_to_the_extraction_model() -> None:
    assert _settings(llm_model="m").model_for_search() == "m"


def test_the_search_model_can_differ_from_the_extraction_model() -> None:
    """Reading 24 pages and running one query are different jobs."""
    settings = _settings(llm_model="cheap", search_model="strong")

    assert settings.model_for_search() == "strong"
    assert settings.llm_model == "cheap"
