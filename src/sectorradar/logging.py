"""structlog configuration, applied once from ``cli.py``.

Logs go to stderr so that stdout stays clean for command output a caller might
want to pipe.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def configure(level: str = "info", *, json_logs: bool = False) -> None:
    """Configure structlog for the process. Safe to call more than once."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=_LEVELS.get(level, logging.INFO),
        force=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVELS.get(level, logging.INFO)),
        # Route through stdlib logging rather than binding a stream directly.
        # A factory holding its own reference to sys.stderr keeps writing to
        # whatever that was at configure() time, so anything that later
        # replaces the stream — a test runner capturing output, a daemonised
        # process — leaves the logger writing to a closed file. The stdlib
        # handler resolves its stream at emit time, and basicConfig(force=True)
        # above rebinds it cleanly on every reconfigure.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
