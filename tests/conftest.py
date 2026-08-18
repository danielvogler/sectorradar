"""Shared fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from sectorradar import db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a fresh, migrated database."""
    path = tmp_path / "radar.db"
    db.init_db(path)
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open connection to a fresh, migrated database."""
    with db.connect(db_path) as connection:
        yield connection


@pytest.fixture
def segments_dir(tmp_path: Path) -> Path:
    """An empty directory to write throwaway segment YAML into."""
    directory = tmp_path / "segments"
    directory.mkdir()
    return directory
