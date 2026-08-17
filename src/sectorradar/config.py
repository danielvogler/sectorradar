"""Environment and segment configuration, validated on load.

An invalid segment YAML must fail loudly at CLI start, naming the field that is
wrong — not halfway through a crawl, after twenty minutes of requests.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LogLevel = Literal["debug", "info", "warning", "error"]

SEGMENTS_DIR = Path("segments")


class ConfigError(RuntimeError):
    """Raised for a configuration problem a user can act on.

    ``cli.py`` catches this and prints the message without a traceback.
    """


# --------------------------------------------------------------------------
# Segment definition
# --------------------------------------------------------------------------


class Geo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str = Field(min_length=2, max_length=2)
    cantons: list[str] | None = None


class SourceConfig(BaseModel):
    """Per-source switches. Sources read the keys they care about."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False


class GoldEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=3)
    expected_tier: int | None = Field(default=None, ge=1, le=4)


class Segment(BaseModel):
    """A market segment: the entire genericity mechanism of this project.

    Adding a segment is adding one of these as a YAML file. If it ever requires
    a code change, the abstraction is wrong and the abstraction is what to fix.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    geo: Geo
    inclusion: str = Field(min_length=20)
    tiers: dict[int, str]
    enrich_tiers: list[int] = Field(default_factory=lambda: [1, 2])
    facets: dict[str, list[str]] = Field(default_factory=dict)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    gold_set: list[GoldEntry] = Field(default_factory=list)

    @field_validator("tiers")
    @classmethod
    def _tiers_are_1_to_4(cls, v: dict[int, str]) -> dict[int, str]:
        bad = [k for k in v if k not in (1, 2, 3, 4)]
        if bad:
            msg = f"tiers must be numbered 1-4, got {bad}"
            raise ValueError(msg)
        return v

    @field_validator("enrich_tiers")
    @classmethod
    def _enrich_tiers_are_known(cls, v: list[int]) -> list[int]:
        bad = [t for t in v if t not in (1, 2, 3, 4)]
        if bad:
            msg = f"enrich_tiers must reference tiers 1-4, got {bad}"
            raise ValueError(msg)
        return v

    def source(self, name: str) -> SourceConfig:
        """Config for one source, defaulting to disabled if absent."""
        return self.sources.get(name, SourceConfig(enabled=False))

    def enabled_sources(self) -> list[str]:
        return sorted(n for n, c in self.sources.items() if c.enabled)

    def gold_domains(self) -> set[str]:
        return {e.domain.lower().removeprefix("www.") for e in self.gold_set}


def segment_path(slug: str, *, base: Path | None = None) -> Path:
    return (base or SEGMENTS_DIR) / f"{slug}.yaml"


def load_segment(slug: str, *, base: Path | None = None) -> Segment:
    """Load and validate one segment YAML.

    Raises ``ConfigError`` with a message that names the offending field, so the
    CLI can print something a human can act on.
    """
    path = segment_path(slug, base=base)
    if not path.exists():
        available = sorted(p.stem for p in (base or SEGMENTS_DIR).glob("*.yaml"))
        msg = f"no segment file at {path}"
        if available:
            msg += f" — available segments: {', '.join(available)}"
        raise ConfigError(msg)

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path} is not valid YAML: {exc}"
        raise ConfigError(msg) from exc

    if not isinstance(raw, dict):
        msg = f"{path} must contain a YAML mapping, got {type(raw).__name__}"
        raise ConfigError(msg)

    try:
        segment = Segment.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()
        )
        msg = f"{path} is not a valid segment — {problems}"
        raise ConfigError(msg) from exc

    if segment.slug != slug:
        msg = f"{path} declares slug '{segment.slug}' but the file is named '{slug}.yaml'"
        raise ConfigError(msg)
    return segment


def available_segments(*, base: Path | None = None) -> list[str]:
    directory = base or SEGMENTS_DIR
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


# --------------------------------------------------------------------------
# Process settings
# --------------------------------------------------------------------------


class Settings(BaseModel):
    """Everything read from the environment. No secrets live in source."""

    model_config = ConfigDict(extra="forbid")

    contact: str | None = None
    db_path: Path = Path("data/radar.db")
    log_level: LogLevel = "info"

    llm_provider: str = "vertex"
    llm_model: str = "gemini-2.5-flash-lite"
    search_provider: str = "vertex_grounding"

    gcp_project: str | None = None
    gcp_location: str = "global"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    exa_api_key: str | None = None
    brave_api_key: str | None = None
    tavily_api_key: str | None = None

    @property
    def raw_dir(self) -> Path:
        return self.db_path.parent / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.db_path.parent / "cache"

    @property
    def export_dir(self) -> Path:
        return self.db_path.parent / "exports"

    def user_agent(self) -> str:
        """The crawler's identity.

        Refuses to build a generic UA: if the operator has not supplied a
        contact address, the correct behaviour is to not crawl at all.
        """
        if not self.contact:
            msg = (
                "SECTORRADAR_CONTACT is unset. The crawler identifies itself with a "
                "real contact address so that a site owner who objects can reach you. "
                "Set it in .env before fetching."
            )
            raise ConfigError(msg)
        return f"sectorradar/0.1 (+https://github.com/danielvogler/sectorradar; {self.contact})"

    def has_llm_credentials(self) -> bool:
        match self.llm_provider:
            case "vertex":
                return self.gcp_project is not None
            case "anthropic":
                return self.anthropic_api_key is not None
            case "openai":
                return self.openai_api_key is not None
            case _:
                return False


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def load_settings(*, env_file: Path | None = None) -> Settings:
    """Read ``.env`` and the environment into a validated ``Settings``."""
    load_dotenv(dotenv_path=env_file, override=False)

    level = (_env("SECTORRADAR_LOG_LEVEL") or "info").lower()
    if level not in ("debug", "info", "warning", "error"):
        msg = f"SECTORRADAR_LOG_LEVEL must be one of debug|info|warning|error, got '{level}'"
        raise ConfigError(msg)

    return Settings(
        contact=_env("SECTORRADAR_CONTACT"),
        db_path=Path(_env("SECTORRADAR_DB_PATH") or "data/radar.db"),
        log_level=level,  # type: ignore[arg-type]  # narrowed by the check above
        llm_provider=_env("SECTORRADAR_LLM_PROVIDER") or "vertex",
        llm_model=_env("SECTORRADAR_LLM_MODEL") or "gemini-2.5-flash-lite",
        search_provider=_env("SECTORRADAR_SEARCH_PROVIDER") or "vertex_grounding",
        gcp_project=_env("GOOGLE_CLOUD_PROJECT"),
        gcp_location=_env("GOOGLE_CLOUD_LOCATION") or "global",
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        exa_api_key=_env("EXA_API_KEY"),
        brave_api_key=_env("BRAVE_API_KEY"),
        tavily_api_key=_env("TAVILY_API_KEY"),
    )
