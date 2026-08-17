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
from typing import Annotated

import typer

from sectorradar import __version__, db
from sectorradar import classify as classify_mod
from sectorradar import discover as discover_mod
from sectorradar import export as export_mod
from sectorradar import extract as extract_mod
from sectorradar import fetch as fetch_mod
from sectorradar import geocode as geocode_mod
from sectorradar import llm as llm_mod
from sectorradar import logging as slogging
from sectorradar import resolve as resolve_mod
from sectorradar import stats as stats_mod
from sectorradar.config import (
    ConfigError,
    Segment,
    Settings,
    available_segments,
    load_segment,
    load_settings,
)

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


def _setup(slug: str, *, verbose: bool) -> tuple[Segment, Settings]:
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

    if not settings.db_path.exists():
        _die(f"no database at {settings.db_path} — run `sectorradar init` first")

    return segment, settings


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
    seg, settings = _setup(segment, verbose=verbose)
    try:
        with db.connect(settings.db_path) as conn:
            report = discover_mod.discover(
                conn,
                seg,
                settings,
                sources=[source] if source else None,
                limit=limit,
                dry_run=dry_run,
            )
    except ValueError as exc:
        _die(str(exc))
        return

    for result in report.results:
        status = f"ERROR {result.error}" if result.error else f"{result.new_unique} new"
        print(f"  {result.source:<14} {result.found:>4} found  {status}")
    print(f"discovered {report.total_found} candidates, {report.total_new} new")
    if report.errors:
        raise typer.Exit(EXIT_FAILURE)


@app.command()
def resolve(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Normalise and dedupe candidates into canonical company rows."""
    seg, settings = _setup(segment, verbose=verbose)
    with db.connect(settings.db_path) as conn:
        report = resolve_mod.resolve(conn, seg, dry_run=dry_run)

    print(f"candidates seen     {report.candidates_seen}")
    print(f"companies created   {report.companies_created}")
    print(f"merged into existing {report.merged_into_existing}")
    print(f"rejected            {report.rejected}")
    print(f"flagged as possible duplicates {report.flagged_duplicate}")


@app.command()
def fetch(
    segment: SegmentOpt,
    force: Annotated[bool, typer.Option("--force", help="Re-fetch even if unchanged.")] = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Politely crawl each company's own website."""
    seg, settings = _setup(segment, verbose=verbose)
    try:
        with db.connect(settings.db_path) as conn:
            report = fetch_mod.fetch(conn, seg, settings, force=force, dry_run=dry_run)
    except ConfigError as exc:
        _die(str(exc))
        return

    print(f"companies   {report.companies}")
    print(f"requested   {report.requested}")
    print(f"stored      {report.stored}")
    print(f"unchanged   {report.unchanged}")
    print(f"disallowed  {report.disallowed} (robots.txt)")
    print(f"blocked     {report.blocked}")
    print(f"errors      {report.errors}")
    if report.blocked_hosts:
        print(f"hosts that blocked us: {', '.join(report.blocked_hosts)}")


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
    seg, settings = _setup(segment, verbose=verbose)
    if model:
        settings = settings.model_copy(update={"llm_model": model})
    try:
        client = llm_mod.get_client(settings)
        with db.connect(settings.db_path) as conn:
            report = extract_mod.extract(
                conn, seg, settings, client, only_changed=only_changed, dry_run=dry_run
            )
    except ConfigError as exc:
        _die(str(exc))
        return

    print(f"companies          {report.companies}")
    print(f"profiles           {report.profiles}")
    print(f"offerings kept     {report.offerings_kept}")
    print(f"offerings dropped  {report.offerings_dropped}")
    print(f"hallucination rate {report.hallucination_rate:.1%}")
    print(f"failed             {report.failed}")
    print(f"cost               USD {report.usage.cost_usd:.4f}")


@app.command()
def classify(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Assign a tier, a written rationale and facet tags."""
    seg, settings = _setup(segment, verbose=verbose)
    try:
        client = llm_mod.get_client(settings)
        with db.connect(settings.db_path) as conn:
            report = classify_mod.classify(conn, seg, settings, client, dry_run=dry_run)
    except ConfigError as exc:
        _die(str(exc))
        return

    print(f"considered        {report.considered}")
    print(f"classified        {report.classified}")
    print(f"undecided         {report.undecided}")
    print(f"out of area       {report.out_of_area}")
    print(f"failed            {report.failed}")
    print(f"skipped, reviewed {report.skipped_reviewed}")
    print(f"by tier           {report.by_tier}")
    print(f"cost              USD {report.usage.cost_usd:.4f}")


@app.command()
def geocode(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Turn addresses into coordinates, cache-first."""
    seg, settings = _setup(segment, verbose=verbose)
    try:
        with db.connect(settings.db_path) as conn:
            report = geocode_mod.geocode(conn, seg, settings, dry_run=dry_run)
    except ConfigError as exc:
        _die(str(exc))
        return

    print(f"considered      {report.considered}")
    print(f"geocoded        {report.geocoded}")
    print(f"from cache      {report.from_cache}")
    print(f"no address yet  {report.skipped_no_address}")
    print(f"failed          {report.failed}")


@app.command()
def run(segment: SegmentOpt, dry_run: DryRunOpt = False, verbose: VerboseOpt = False) -> None:
    """Run every stage in dependency order.

    Each stage commits before the next begins, so an interrupted run leaves a
    consistent database and re-running resumes rather than restarting.
    """
    seg, settings = _setup(segment, verbose=verbose)

    try:
        client = llm_mod.get_client(settings)
    except ConfigError as exc:
        _die(str(exc))
        return

    try:
        with db.connect(settings.db_path) as conn:
            print("== discover ==")
            found = discover_mod.discover(conn, seg, settings, dry_run=dry_run)
            print(f"   {found.total_found} candidates, {found.total_new} new")

            print("== resolve ==")
            resolved = resolve_mod.resolve(conn, seg, dry_run=dry_run)
            print(f"   {resolved.companies_created} new companies, {resolved.rejected} rejected")

            print("== fetch ==")
            fetched = fetch_mod.fetch(conn, seg, settings, dry_run=dry_run)
            print(f"   {fetched.stored} pages stored, {fetched.unchanged} unchanged")

            print("== extract ==")
            extracted = extract_mod.extract(
                conn, seg, settings, client, only_changed=True, dry_run=dry_run
            )
            print(
                f"   {extracted.offerings_kept} offerings kept, "
                f"{extracted.offerings_dropped} dropped "
                f"({extracted.hallucination_rate:.0%} unsupported)"
            )

            # Geocode BEFORE classify: the geography gate in classify uses the
            # geocoder's verdict as its evidence, and the geocoder only accepts
            # results inside the segment's country.
            print("== geocode ==")
            located = geocode_mod.geocode(conn, seg, settings, dry_run=dry_run)
            print(f"   {located.geocoded} geocoded")

            print("== classify ==")
            classified = classify_mod.classify(conn, seg, settings, client, dry_run=dry_run)
            print(f"   {classified.by_tier}  ({classified.out_of_area} excluded on geography)")

            cost = extracted.usage.cost_usd + classified.usage.cost_usd
            print(f"\nLLM cost this run: USD {cost:.4f}")
    except (ConfigError, ValueError) as exc:
        # ValueError reaches here from a bad source name. The contract is a
        # readable message and exit 1, never a traceback.
        _die(str(exc))
        return
    except KeyboardInterrupt:
        print("\ninterrupted — the database is consistent, re-run to resume", file=sys.stderr)
        raise typer.Exit(EXIT_FAILURE) from None


@app.command()
def stats(
    segment: SegmentOpt,
    recall_only: Annotated[
        bool, typer.Option("--recall-only", help="Print gold-set recall and nothing else.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Report saturation, gold-set recall and cost."""
    seg, settings = _setup(segment, verbose=verbose)
    with db.connect(settings.db_path, read_only=True) as conn:
        collected = stats_mod.collect(conn, seg)

    if recall_only:
        # A bare number, so `make verify` can compare it without parsing prose.
        print(f"{collected.recall.percent}")
        return
    print(stats_mod.format_report(collected))


@app.command()
def snapshot(segment: SegmentOpt, verbose: VerboseOpt = False) -> None:
    """Freeze the accepted set so change over time is reconstructable."""
    seg, settings = _setup(segment, verbose=verbose)
    with db.connect(settings.db_path) as conn:
        captured = export_mod.snapshot(conn, seg)
    print(f"snapshot taken: {captured} companies")


@app.command()
def export(
    segment: SegmentOpt,
    fmt: Annotated[str, typer.Option("--format", help="csv | xlsx | geojson")] = "csv",
    verbose: VerboseOpt = False,
) -> None:
    """Write the accepted set to a file."""
    seg, settings = _setup(segment, verbose=verbose)
    try:
        with db.connect(settings.db_path, read_only=True) as conn:
            path = export_mod.export(conn, seg, settings.export_dir, fmt=fmt)
    except ValueError as exc:
        _die(str(exc))
        return
    print(f"wrote {path}")


if __name__ == "__main__":  # pragma: no cover
    app()
