"""Command line entry point.

Every pipeline stage is exposed as its own subcommand so a run can be resumed at
any point; ``sectorradar run`` chains them in dependency order.

Exit codes, per the operating contract:

* ``0`` success
* ``1`` a handled failure, reported as a readable message with no traceback
* ``2`` bad usage, or a stage that is not implemented yet

No stack trace reaches the user unless ``--verbose`` is set.
"""

from __future__ import annotations

import platform
import sqlite3
import sys
from pathlib import Path
from typing import Annotated

import typer

from sectorradar import __version__, db
from sectorradar import logging as slogging
from sectorradar.config import ConfigError, Segment, available_segments, load_segment, load_settings

app = typer.Typer(
    name="sectorradar",
    help=(
        "Turn a market segment into a structured, browsable dataset. "
        "Gathers publicly available company information and organises it with "
        "source citations."
    ),
    no_args_is_help=True,
    add_completion=False,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

SegmentOpt = Annotated[
    str, typer.Option("--segment", "-s", help="Segment slug, e.g. agentic-ai-ch")
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging and tracebacks.")]
DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", help="Report what would happen, write nothing.")
]


def _version_callback(value: bool) -> None:
    if value:
        print(f"sectorradar {__version__}")
        raise typer.Exit(EXIT_OK)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Shared options for every subcommand."""


def _die(message: str) -> None:
    """Report a handled failure the way the contract requires and stop."""
    print(f"error: {message}", file=sys.stderr)
    raise typer.Exit(EXIT_FAILURE)


def _setup(slug: str, *, verbose: bool) -> tuple[Segment, Path]:
    """Load settings and the segment, or exit 1 with something actionable."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        _die(str(exc))
        raise AssertionError from None  # unreachable; _die always raises

    slogging.configure("debug" if verbose else settings.log_level)

    try:
        segment = load_segment(slug)
    except ConfigError as exc:
        _die(str(exc))
        raise AssertionError from None

    return segment, settings.db_path


def _not_implemented(stage: str) -> None:
    """Placeholder for a stage whose phase has not landed yet.

    Exits 2 rather than 0 so that a script chaining stages fails loudly instead
    of silently believing the work happened.
    """
    print(f"error: `sectorradar {stage}` is not implemented yet", file=sys.stderr)
    raise typer.Exit(EXIT_USAGE)


# ---------------------------------------------------------------------------
# Implemented
# ---------------------------------------------------------------------------


@app.command()
def init(verbose: VerboseOpt = False) -> None:
    """Create the database and apply any pending schema migrations."""
    settings = load_settings()
    slogging.configure("debug" if verbose else settings.log_level)
    applied = db.init_db(settings.db_path)
    if applied:
        print(f"applied {applied} migration(s) — schema is now v{db.SCHEMA_VERSION}")
    else:
        print(f"schema already current at v{db.SCHEMA_VERSION}")
    print(f"database: {settings.db_path}")


@app.command()
def doctor() -> None:
    """Check the environment, the database and the configured providers."""
    settings = load_settings()

    print(f"sectorradar {__version__}")
    print(f"python      {platform.python_version()} ({sys.platform})")
    print()

    contact = settings.contact or "UNSET — the crawler will refuse to run (see .env.example)"
    print(f"contact     {contact}")
    print(f"llm         {settings.llm_provider} / {settings.llm_model}")
    print(f"search      {settings.search_provider}")
    creds = "present" if settings.has_llm_credentials() else "MISSING"
    print(f"llm creds   {creds}")
    print()

    segments = available_segments()
    print(f"segments    {', '.join(segments) if segments else 'none found in segments/'}")

    print(f"database    {settings.db_path}", end="")
    if not settings.db_path.exists():
        print("  (absent — run `sectorradar init`)")
        return
    print()

    with db.connect(settings.db_path, read_only=True) as conn:
        version = db.current_version(conn)
        expected = db.SCHEMA_VERSION
        state = "current" if version == expected else f"BEHIND (expected v{expected})"
        print(f"schema      v{version} {state}")
        for table in ("company", "membership", "candidate", "offering", "company_field", "page"):
            try:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # noqa: S608
            except sqlite3.Error:
                print(f"  {table:<14} -")
                continue
            print(f"  {table:<14} {row['n']}")


# ---------------------------------------------------------------------------
# Pipeline stages — the surface is fixed now so nothing downstream has to guess
# ---------------------------------------------------------------------------


@app.command()
def discover(
    segment: SegmentOpt,
    source: Annotated[str | None, typer.Option("--source", help="Run only this source.")] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Stop after N candidates.")] = None,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Run discovery sources and record candidate companies."""
    _setup(segment, verbose=verbose)
    _not_implemented("discover")


@app.command()
def resolve(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Normalise and dedupe candidates into canonical company rows."""
    _setup(segment, verbose=verbose)
    _not_implemented("resolve")


@app.command()
def fetch(
    segment: SegmentOpt,
    force: Annotated[bool, typer.Option("--force", help="Re-fetch even if unchanged.")] = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Politely crawl each company's own website."""
    _setup(segment, verbose=verbose)
    _not_implemented("fetch")


@app.command()
def extract(
    segment: SegmentOpt,
    model: Annotated[
        str | None, typer.Option("--model", help="Override the configured model.")
    ] = None,
    only_changed: Annotated[
        bool, typer.Option("--only-changed", help="Skip unchanged pages.")
    ] = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Extract a structured, evidence-carrying profile for each company."""
    _setup(segment, verbose=verbose)
    _not_implemented("extract")


@app.command()
def classify(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Assign a tier, a written rationale and facet tags."""
    _setup(segment, verbose=verbose)
    _not_implemented("classify")


@app.command()
def geocode(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Turn addresses into coordinates, cache-first."""
    _setup(segment, verbose=verbose)
    _not_implemented("geocode")


@app.command()
def run(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Run every stage in dependency order."""
    _setup(segment, verbose=verbose)
    _not_implemented("run")


@app.command()
def stats(
    segment: SegmentOpt,
    recall_only: Annotated[
        bool, typer.Option("--recall-only", help="Print gold-set recall and nothing else.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Report saturation, gold-set recall and cost."""
    _setup(segment, verbose=verbose)
    _not_implemented("stats")


@app.command()
def snapshot(segment: SegmentOpt, verbose: VerboseOpt = False) -> None:
    """Freeze the accepted set so change over time is reconstructable."""
    _setup(segment, verbose=verbose)
    _not_implemented("snapshot")


@app.command()
def export(
    segment: SegmentOpt,
    fmt: Annotated[str, typer.Option("--format", help="csv | xlsx | geojson")] = "csv",
    verbose: VerboseOpt = False,
) -> None:
    """Write the accepted set to a file."""
    _setup(segment, verbose=verbose)
    _not_implemented("export")


if __name__ == "__main__":  # pragma: no cover
    app()
