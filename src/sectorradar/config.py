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

#: Segments you would rather not announce you are mapping. Gitignored, and
#: searched after the shared directory so a public example never shadows one of
#: yours. A directory rather than a list of ignored filenames, because a
#: gitignore naming somebody's private segments tells you what they are.
PRIVATE_SEGMENTS_DIR = Path("segments/private")


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

    #: The market, written as a person would say it. This is the page headline
    #: and the browser title, not an internal label — validated because a slug
    #: pasted in here passes every type check and then greets everybody who
    #: opens the result.
    #:
    #: Convention: "<what they do>, <where>". The front end splits on the last
    #: comma and sets the region on its own line under the subject, so a name
    #: written that way typesets correctly at any width.
    name: str = Field(min_length=1)
    geo: Geo
    #: Injected verbatim into the classifier prompt *and* shown under the
    #: headline as the definition every figure on the page is relative to. It
    #: is doing two jobs, and both want the same thing: instructions a careful
    #: colleague could follow.
    inclusion: str = Field(min_length=20)
    tiers: dict[int, str]
    enrich_tiers: list[int] = Field(default_factory=lambda: [1, 2])
    #: Controlled vocabularies the classifier picks from. Two shapes are
    #: accepted, and the second is what makes a segment portable:
    #:
    #:     facets:
    #:       service_type: [pentest, incident_response]        # values only
    #:
    #:     facets:
    #:       service_type:                                     # values + the
    #:         pentest: [penetrationstest, "penetration test"] # words that
    #:         incident_response: [notfall, forensik]          # ground them
    #:
    #: A tag has to be traceable to something the site said, and the words that
    #: prove it are market-specific: "pentest" never appears on a page that
    #: says "Penetrationstest". Keeping those words in Python meant a new
    #: segment inherited the agentic-AI vocabulary and silently dropped almost
    #: every tag it declared.
    facets: dict[str, list[str] | dict[str, list[str]]] = Field(default_factory=dict)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    gold_set: list[GoldEntry] = Field(default_factory=list)

    #: Domains belonging to whoever is running this. They are part of the
    #: market and belong in the dataset, but they are not competitors, and a
    #: comparison that silently averages you in with the field answers the
    #: wrong question.
    own_domains: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_reads_as_a_market(cls, v: str) -> str:
        """The name is the page headline, so it has to survive being read.

        Every failure here is something that type-checks and then renders as
        the title of somebody's market map: a slug, a placeholder, a single
        word. Cheap to catch at load, embarrassing to catch in a screenshot.
        """
        name = v.strip()
        if not name:
            msg = "name is empty. It is the page headline — write the market as you would say it"
            raise ValueError(msg)
        if "-" in name and " " not in name:
            msg = (
                f"name {name!r} looks like a slug. It is the page headline and the browser "
                "title, so write it as a person would say it — for example "
                "'Cybersecurity consultancies, Austria'"
            )
            raise ValueError(msg)
        if len(name) < 8 or " " not in name:
            msg = (
                f"name {name!r} is too short to describe a market. Convention is "
                "'<what they do>, <where>' — the page sets the part after the last "
                "comma on its own line"
            )
            raise ValueError(msg)
        return name

    @field_validator("inclusion")
    @classmethod
    def _inclusion_reads_as_instructions(cls, v: str) -> str:
        """It goes into the classifier prompt, so it has to instruct something.

        A description of a market ("Swiss AI companies") tells a model nothing
        about where the edge is, and the edge is the entire job. The word
        "Include" is a low bar, but it is the difference between a rule and a
        label.
        """
        text = " ".join(v.split())
        if "include" not in text.casefold():
            msg = (
                "inclusion does not say what to include. It is injected verbatim into the "
                "classifier prompt, so write it as instructions — 'Include a company if it "
                "offers, as a named service on its own website, ...' — not as a description "
                "of the market. See segments/AGENTS.md."
            )
            raise ValueError(msg)
        return text

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

    def facet_values(self, facet: str) -> list[str]:
        """The permitted values for one facet, whichever shape it was written in."""
        declared = self.facets.get(facet)
        if declared is None:
            return []
        return list(declared)

    def facet_keywords(self, facet: str, value: str) -> tuple[str, ...]:
        """Words on a page that count as support for a facet value.

        Empty when the segment supplies none, which leaves the caller to fall
        back — the value itself is a reasonable last resort in English and a
        poor one in German, so a segment that cares supplies its own.
        """
        declared = self.facets.get(facet)
        if isinstance(declared, dict):
            return tuple(declared.get(value, ()))
        return ()

    def source(self, name: str) -> SourceConfig:
        """Config for one source, defaulting to disabled if absent."""
        return self.sources.get(name, SourceConfig(enabled=False))

    def enabled_sources(self) -> list[str]:
        return sorted(n for n, c in self.sources.items() if c.enabled)

    def owned(self) -> set[str]:
        """Normalised domains belonging to the operator."""
        return {d.lower().strip().removeprefix("www.") for d in self.own_domains if d.strip()}

    def gold_domains(self) -> set[str]:
        return {e.domain.lower().removeprefix("www.") for e in self.gold_set}

    def to_yaml(self) -> str:
        """Serialise back to YAML, for recording what a run actually used.

        The stored copy is the validated model rather than the file's bytes, so
        it reflects defaults that were applied rather than only what was typed.
        """
        return yaml.safe_dump(
            self.model_dump(mode="json", exclude_none=False),
            sort_keys=False,
            allow_unicode=True,
        )


def segment_path(slug: str, *, base: Path | None = None) -> Path:
    """Where a segment lives: the shared directory, or the private one.

    Returns the shared path when neither exists, so a missing segment reports
    the location somebody would expect rather than the private one.
    """
    if base is not None:
        return base / f"{slug}.yaml"

    shared = SEGMENTS_DIR / f"{slug}.yaml"
    if shared.exists():
        return shared
    private = PRIVATE_SEGMENTS_DIR / f"{slug}.yaml"
    return private if private.exists() else shared


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path} is not valid YAML: {exc}"
        raise ConfigError(msg) from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        msg = f"{path} must contain a YAML mapping, got {type(raw).__name__}"
        raise ConfigError(msg)
    return raw


def merge_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Lay one mapping over another, recursing into nested mappings.

    Lists are replaced wholesale rather than concatenated. Appending would make
    it impossible to *remove* a seed or a query in an overlay, and a config
    layer you can only ever add to stops being useful about the third time you
    use it.
    """
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_overlay(current, value)
        else:
            merged[key] = value
    return merged


def load_segment(slug: str, *, base: Path | None = None) -> Segment:
    """Load and validate one segment YAML, plus any local overlay beside it.

    A segment file is meant to be shareable — it describes a market, and that
    description is the interesting part of this repository. What is *not*
    shareable is which companies in that market happen to be yours, and which
    private notes you keep against a seed. Those live in a gitignored
    ``<slug>.local.yaml`` that is merged over the committed file, so the public
    config stays a worked example and nothing personal has to be redacted by
    hand before pushing.

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

    raw = _read_mapping(path)

    local = path.with_suffix(".local.yaml")
    if local.exists():
        raw = merge_overlay(raw, _read_mapping(local))

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
    """Every segment defined, excluding local overlays.

    `<slug>.local.yaml` is a fragment merged over a real segment file, not a
    segment. Globbing `*.yaml` picked it up as one and then failed validation
    on it for missing every required field — which is a confusing way to learn
    that your private overlay is being treated as a public config.
    """
    directories = [base] if base is not None else [SEGMENTS_DIR, PRIVATE_SEGMENTS_DIR]
    found = {
        path.stem
        for directory in directories
        if directory.exists()
        for path in directory.glob("*.yaml")
        if not path.name.endswith(".local.yaml")
    }
    return sorted(found)


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
    #: Model used for grounded search, when the search provider is a model
    #: rather than an index. Left unset it follows `llm_model`, which is what
    #: it silently did before — and that was wrong in both directions: reading
    #: 24 pages of a website and running one search query are different jobs
    #: with different price and quality trade-offs, and tuning one changed the
    #: other with no way to tell.
    search_model: str | None = None

    gcp_project: str | None = None
    gcp_location: str = "global"

    # Publishing. Set these and `make app` pulls the published dataset instead
    # of exporting from a local database — which is what somebody who wants to
    # look at the data, rather than collect it, actually needs.
    gcs_bucket: str | None = None
    gcs_location: str = "europe-west6"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    exa_api_key: str | None = None
    brave_api_key: str | None = None
    tavily_api_key: str | None = None

    def model_for_search(self) -> str:
        """The search model, falling back to the extraction model."""
        return self.search_model or self.llm_model

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
        search_model=_env("SECTORRADAR_SEARCH_MODEL"),
        # Either name. `GOOGLE_CLOUD_PROJECT` is what gcloud and the client
        # libraries already read, so it is often set on a machine before this
        # tool is; `SECTORRADAR_GCP_PROJECT` is the house prefix and wins when
        # both are present. Reading only one of them meant the pipeline ran
        # and `make bucket` refused, for the same account.
        gcp_project=_env("SECTORRADAR_GCP_PROJECT") or _env("GOOGLE_CLOUD_PROJECT"),
        gcp_location=_env("GOOGLE_CLOUD_LOCATION") or "global",
        gcs_bucket=_env("SECTORRADAR_GCS_BUCKET"),
        gcs_location=_env("SECTORRADAR_GCS_LOCATION") or "europe-west6",
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        exa_api_key=_env("EXA_API_KEY"),
        brave_api_key=_env("BRAVE_API_KEY"),
        tavily_api_key=_env("TAVILY_API_KEY"),
    )
