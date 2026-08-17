"""The CLI surface and its exit-code contract.

Exit codes matter more than output here: ``0`` success, ``1`` a handled failure
with a readable message, ``2`` bad usage or an unimplemented stage. A stub that
exits 0 would let a chained script believe work happened.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sectorradar.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, app

runner = CliRunner()

# Every stage that is declared but not yet implemented.
STUB_STAGES = [
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
    for stage in [*STUB_STAGES, "init", "doctor"]:
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


@pytest.mark.parametrize("stage", STUB_STAGES)
def test_unimplemented_stages_exit_2(stage: str) -> None:
    result = runner.invoke(app, [stage, "--segment", "agentic-ai-ch"])
    assert result.exit_code == EXIT_USAGE, f"{stage} should exit {EXIT_USAGE}"
    assert "not implemented" in result.output


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
