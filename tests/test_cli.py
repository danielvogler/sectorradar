"""The CLI surface and its exit-code contract.

Exit codes matter more than output here: ``0`` success, ``1`` a handled failure
with a readable message, ``2`` bad usage or an unimplemented stage. A stub that
exits 0 would let a chained script believe work happened.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sectorradar.cli import EXIT_FAILURE, EXIT_OK, app

runner = CliRunner()

# Every pipeline stage. All of them are implemented and all of them need a
# database, so the shared contract to check is how they behave without one.
ALL_STAGES = [
    "discover",
    "resolve",
    "fetch",
    "extract",
    "classify",
    "geocode",
    "run",
    "stats",
    "snapshot",
    "export",
]


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test touch the developer's real database."""
    monkeypatch.setenv("SECTORRADAR_DB_PATH", str(tmp_path / "radar.db"))


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == EXIT_OK
    assert "sectorradar" in result.output


def test_help_lists_every_pipeline_stage() -> None:
    """The surface is fixed early so nothing downstream has to guess it."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == EXIT_OK
    for stage in [*ALL_STAGES, "init", "doctor"]:
        assert stage in result.output


def test_init_creates_the_database(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == EXIT_OK
    assert (tmp_path / "radar.db").exists()
    assert "migration" in result.output


def test_init_is_idempotent() -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])
    assert result.exit_code == EXIT_OK
    assert "already current" in result.output


def test_doctor_on_a_missing_database_says_what_to_run() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == EXIT_OK
    assert "sectorradar init" in result.output


def test_doctor_reports_row_counts_once_initialised() -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == EXIT_OK
    assert "company" in result.output
    assert "schema" in result.output


def test_doctor_flags_a_missing_contact_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The crawler refuses to run without one, so doctor must say so plainly."""
    monkeypatch.delenv("SECTORRADAR_CONTACT", raising=False)
    monkeypatch.setattr("sectorradar.config.load_dotenv", lambda *a, **k: False)
    result = runner.invoke(app, ["doctor"])
    assert "UNSET" in result.output


@pytest.mark.parametrize("stage", ALL_STAGES)
def test_every_stage_refuses_to_run_without_a_database(stage: str) -> None:
    """Exit 1 with the fix, rather than a traceback from a missing file."""
    result = runner.invoke(app, [stage, "--segment", "agentic-ai-ch"])
    assert result.exit_code == EXIT_FAILURE
    assert "sectorradar init" in result.output


def test_discover_and_resolve_run_end_to_end(tmp_path: Path) -> None:
    """The seeds -> resolve spine, on a throwaway segment."""
    segments = tmp_path / "segments"
    segments.mkdir()
    (segments / "tiny.yaml").write_text(
        """
slug: tiny
name: Tiny test segment
geo:
  country: CH
inclusion: Include a company if it sells widgets as a named service on its site.
tiers:
  1: primary
sources:
  seeds:
    enabled: true
    urls:
      - url: https://example.ch
        name: Example AG
        city: Zurich
        canton: ZH
      - https://other.ch
""",
        encoding="utf-8",
    )

    runner.invoke(app, ["init"])
    import os

    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        found = runner.invoke(app, ["discover", "--segment", "tiny"])
        resolved = runner.invoke(app, ["resolve", "--segment", "tiny"])
    finally:
        os.chdir(cwd)

    assert found.exit_code == EXIT_OK, found.output
    assert "2 new" in found.output or "2 candidates" in found.output
    assert resolved.exit_code == EXIT_OK, resolved.output
    assert "companies created   2" in resolved.output


def test_an_unknown_segment_fails_readably_without_a_traceback() -> None:
    result = runner.invoke(app, ["resolve", "--segment", "does-not-exist"])
    assert result.exit_code == EXIT_FAILURE
    assert "no segment file" in result.output
    assert "Traceback" not in result.output


def test_an_unknown_segment_lists_the_real_ones() -> None:
    result = runner.invoke(app, ["resolve", "--segment", "does-not-exist"])
    assert "agentic-ai-ch" in result.output


def test_a_bad_log_level_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECTORRADAR_LOG_LEVEL", "chatty")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != EXIT_OK


def test_a_stale_schema_is_reported_rather_than_crashing(tmp_path: Path) -> None:
    """A database from an older build must not surface as an OperationalError.

    Real failure: adding a column and running a stage against the existing
    database produced a traceback from deep inside geocode saying
    "no such column: geocode_status", which tells the user nothing they can act
    on. The fix they need is `sectorradar init`.
    """
    import sqlite3

    from sectorradar import db

    path = tmp_path / "radar.db"
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM schema_version WHERE version = ?", (db.SCHEMA_VERSION,))
        conn.commit()

    result = runner.invoke(app, ["geocode", "--segment", "agentic-ai-ch"])

    assert result.exit_code == EXIT_FAILURE
    assert "sectorradar init" in result.output
    assert "schema" in result.output.lower()
    assert "Traceback" not in result.output
