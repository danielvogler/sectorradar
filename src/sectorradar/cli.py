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
from sectorradar import audit as audit_mod
from sectorradar import classify as classify_mod
from sectorradar import deepen as deepen_mod
from sectorradar import discover as discover_mod
from sectorradar import export as export_mod
from sectorradar import extract as extract_mod
from sectorradar import fetch as fetch_mod
from sectorradar import geocode as geocode_mod
from sectorradar import llm as llm_mod
from sectorradar import logging as slogging
from sectorradar import publish as publish_mod
from sectorradar import resolve as resolve_mod
from sectorradar import seo as seo_mod
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
    str, typer.Option("--segment", "-s", help="Segment slug, e.g. pilates-zurich")
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


def _setup(slug: str, *, verbose: bool, needs_db: bool = True) -> tuple[Segment, Settings]:
    """Load settings and the segment, or exit 1 with something actionable.

    ``needs_db=False`` is for the commands somebody runs who has never
    collected anything — pulling a published dataset should not demand a
    database they were never going to have.
    """
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

    if not needs_db:
        return segment, settings

    if not settings.db_path.exists():
        _die(f"no database at {settings.db_path} — run `sectorradar init` first")

    # A database created by an older build is missing columns this one writes
    # to, and the failure without this check is an OperationalError traceback
    # from somewhere deep in a stage, which tells the user nothing actionable.
    with db.connect(settings.db_path, read_only=True) as conn:
        version = db.current_version(conn)
    if version < db.SCHEMA_VERSION:
        _die(
            f"database schema is v{version} but this build expects "
            f"v{db.SCHEMA_VERSION} — run `sectorradar init` to migrate"
        )

    return segment, settings


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
    print(f"excluded          {report.excluded}")
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
def deepen(
    segment: SegmentOpt,
    max_rounds: Annotated[int, typer.Option(help="Ceiling on discovery rounds")] = 6,
    max_spend: Annotated[float, typer.Option(help="Ceiling on USD spent widening queries")] = 5.0,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Keep discovering until the market stops giving.

    Runs discovery, measures how much of what came back was new, and if the
    market is still producing it writes fresh queries and goes again. Stops on
    saturation, a round cap or a spend cap — and says which.

    This exists so that "did we look hard enough" is not left to whoever is
    watching. A person gets bored and an agent decides it has done enough, both
    silently, and a half-searched market produces a dataset that looks
    complete.
    """
    seg, settings = _setup(segment, verbose=verbose)
    try:
        client = llm_mod.get_client(settings)
    except ConfigError as exc:
        _die(str(exc))
        return

    with db.connect(settings.db_path) as conn:
        report = deepen_mod.deepen(
            conn,
            seg,
            settings,
            client,
            max_rounds=max_rounds,
            max_spend_usd=max_spend,
            dry_run=dry_run,
        )
    print(report.render())


@app.command()
def audit(segment: SegmentOpt, verbose: VerboseOpt = False) -> None:
    """Report what this dataset is probably missing.

    Free, offline, and safe to run at any time. Printed automatically at the
    end of `run`, because every gap this tool has had was found by a person
    noticing something looked wrong — and nobody running it for the first time
    knows what wrong looks like.
    """
    seg, settings = _setup(segment, verbose=verbose)
    with db.connect(settings.db_path, read_only=True) as conn:
        report = audit_mod.audit(conn, seg)

    print(report.render())
    if report.findings:
        counts: dict[str, int] = {}
        for finding in report.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        print()
        print(
            ", ".join(f"{n} {sev}" for sev, n in counts.items()) + " — none of these fail a build"
        )


@app.command(name="seo")
def seo_cmd(segment: SegmentOpt, verbose: VerboseOpt = False) -> None:
    """Measure search visibility from the markup already on disk.

    Costs nothing and calls nothing: every signal is in HTML the crawler
    already stored, so this can be re-run as often as you like.
    """
    seg, settings = _setup(segment, verbose=verbose)
    with db.connect(settings.db_path) as conn:
        report = seo_mod.analyse_segment(conn, seg)

    print(f"companies analysed {report.companies}")
    print(f"no pages stored    {report.skipped}")
    print(f"median score       {report.median_score}")
    print(f"blocking indexing  {report.blocking_indexing}")


@app.command()
def publish(
    segment: SegmentOpt,
    bucket: Annotated[str | None, typer.Option(help="Target bucket, without gs://")] = None,
    project: Annotated[str | None, typer.Option(help="GCP project id")] = None,
    execute: Annotated[bool, typer.Option(help="Actually upload. Default is a dry run.")] = False,
    site: Annotated[bool, typer.Option(help="Also upload the built site")] = True,
    verbose: VerboseOpt = False,
) -> None:
    """Publish the exported dataset to Google Cloud Storage.

    Prints what it would send and writes nothing unless ``--execute`` is given.
    Uploading to shared storage is outward-facing and awkward to take back, so
    it is a deliberate act rather than a default.
    """
    seg, settings = _setup(segment, verbose=verbose)

    target = bucket or settings.gcs_bucket
    gcp_project = project or settings.gcp_project
    if not target:
        _die("no bucket: pass --bucket or set SECTORRADAR_GCS_BUCKET in .env")
        return
    if not gcp_project:
        _die("no project: pass --project or set SECTORRADAR_GCP_PROJECT in .env")
        return

    try:
        plan = publish_mod.plan(
            bucket=target,
            project=gcp_project,
            data_file=Path("web/src/data") / f"{seg.slug}.web.json",
            site_dir=Path("web/dist") if site else None,
            slug=seg.slug,
        )
    except publish_mod.PublishError as exc:
        _die(str(exc))
        return

    print(plan.describe())
    if not execute:
        print()
        print("dry run — nothing was uploaded. Re-run with --execute to publish.")
        return

    try:
        written = publish_mod.execute(plan)
    except publish_mod.PublishError as exc:
        _die(str(exc))
        return
    print(f"\nuploaded {written} files to gs://{target}")


@app.command(name="pull")
def pull_cmd(
    segment: Annotated[
        str | None,
        typer.Option("--segment", "-s", help="Segment slug. Omitted, lists what is published."),
    ] = None,
    bucket: Annotated[str | None, typer.Option(help="Source bucket, without gs://")] = None,
    project: Annotated[str | None, typer.Option(help="GCP project id")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Download a published dataset so the page can be served locally.

    The counterpart to `publish`: one person runs the crawl, everybody else
    reads the result. It needs no database, no API keys, no crawl — and no
    segment definition. That last one matters: a market somebody keeps private
    is exactly the kind whose *result* is worth sharing, so requiring the
    reader to hold a config file they were never given would make the whole
    arrangement circular. The published document carries the market's own
    definition, which is what lets it be read at all.

    With no `--segment`, lists what the bucket holds and pulls it if there is
    only one.
    """
    try:
        settings = load_settings()
    except ConfigError as exc:
        _die(str(exc))
        return
    slogging.configure("debug" if verbose else settings.log_level)

    target = bucket or settings.gcs_bucket
    gcp_project = project or settings.gcp_project
    if not target:
        _die("no bucket: pass --bucket or set SECTORRADAR_GCS_BUCKET in .env")
        return
    if not gcp_project:
        _die("no project: pass --project or set SECTORRADAR_GCP_PROJECT in .env")
        return

    try:
        slug = segment
        if slug is None:
            published = publish_mod.available(bucket=target, project=gcp_project)
            if not published:
                _die(f"nothing published in gs://{target} yet")
                return
            if len(published) > 1:
                print(f"published in gs://{target}:")
                for name in published:
                    print(f"  {name}")
                print("\npick one with --segment")
                return
            slug = published[0]
            print(f"one dataset published: {slug}")

        path = publish_mod.pull(
            bucket=target,
            project=gcp_project,
            slug=slug,
            destination=Path("web/src/data"),
        )
    except publish_mod.PublishError as exc:
        _die(str(exc))
        return
    print(f"pulled {path} ({path.stat().st_size / 1024:.0f} KiB)")
    print(f"now run: make serve SEGMENT={slug}")


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

            # Unprompted, because nobody running this for the first time knows
            # what a thin result looks like. Every gap this tool has had was
            # found by a person noticing something was off; this is that,
            # written down.
            if not dry_run:
                report = audit_mod.audit(conn, seg)
                print("\n" + "-" * 60)
                print("coverage — what this run is probably missing")
                print("-" * 60)
                print(report.render())
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
