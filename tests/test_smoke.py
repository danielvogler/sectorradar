"""Smoke tests: the package imports and the CLI is wired up."""

from __future__ import annotations

from typer.testing import CliRunner

from sectorradar import __version__
from sectorradar.cli import app

runner = CliRunner()


def test_version_is_pep440_triple() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_help_names_the_tool() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "sectorradar" in result.output


def test_doctor_reports_the_running_version() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert __version__ in result.output
