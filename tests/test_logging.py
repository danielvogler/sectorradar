"""Logging configuration.

The regression guarded here: a logger factory that captures ``sys.stderr`` at
configure() time keeps writing to that object forever. Anything that later
replaces the stream — a test runner capturing output, a daemonised process —
leaves the logger writing to a closed file, and the first log line after that
raises ``ValueError: I/O operation on closed file``.
"""

from __future__ import annotations

import io
import logging as stdlib_logging
import sys

import pytest

from sectorradar import logging as slogging


def test_configure_sets_the_requested_level() -> None:
    slogging.configure("warning")
    assert stdlib_logging.getLogger().level == stdlib_logging.WARNING
    slogging.configure("info")
    assert stdlib_logging.getLogger().level == stdlib_logging.INFO


def test_an_unknown_level_falls_back_to_info() -> None:
    slogging.configure("shouty")
    assert stdlib_logging.getLogger().level == stdlib_logging.INFO


def test_logging_survives_stderr_being_replaced_and_closed() -> None:
    """Configure against one stream, close it, then log against another."""
    captured = io.StringIO()
    original = sys.stderr
    try:
        sys.stderr = captured
        slogging.configure("info")
        slogging.get_logger("test").info("first", step=1)
    finally:
        sys.stderr = original
    captured.close()

    # The stream the logger was configured against is now closed. Reconfiguring
    # must rebind cleanly rather than keep writing into the dead object.
    replacement = io.StringIO()
    try:
        sys.stderr = replacement
        slogging.configure("info")
        slogging.get_logger("test").info("second", step=2)
    finally:
        sys.stderr = original

    assert "second" in replacement.getvalue()


def test_get_logger_emits_structured_key_values() -> None:
    stream = io.StringIO()
    original = sys.stderr
    try:
        sys.stderr = stream
        slogging.configure("info")
        slogging.get_logger("test").info("migration.applied", version=2)
    finally:
        sys.stderr = original

    output = stream.getvalue()
    assert "migration.applied" in output
    assert "2" in output


def test_json_mode_emits_parseable_lines() -> None:
    import json

    stream = io.StringIO()
    original = sys.stderr
    try:
        sys.stderr = stream
        slogging.configure("info", json_logs=True)
        slogging.get_logger("test").info("event.name", count=3)
    finally:
        sys.stderr = original
        slogging.configure("info")  # restore console rendering for other tests

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "event.name"
    assert payload["count"] == 3


@pytest.fixture(autouse=True)
def _restore_logging() -> None:
    """Leave logging in a sane state for whatever runs next."""
    slogging.configure("info")
