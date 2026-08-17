"""Command line entry point.

Every pipeline stage is exposed as a subcommand so that a run can be resumed at
any point. ``sectorradar run`` chains them in dependency order.
"""

from __future__ import annotations

import platform
import sys
from typing import Annotated

import typer

from sectorradar import __version__

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


def _version_callback(value: bool) -> None:
    if value:
        print(f"sectorradar {__version__}")
        raise typer.Exit(0)


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


@app.command()
def doctor() -> None:
    """Check the local environment and report what is and is not ready."""
    print(f"sectorradar {__version__}")
    print(f"python      {platform.python_version()} ({sys.platform})")


if __name__ == "__main__":  # pragma: no cover
    app()
