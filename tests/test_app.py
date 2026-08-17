"""The Streamlit pages actually render.

Serving HTTP 200 only proves the Streamlit shell loaded — a script that raises
still returns 200 and shows the traceback inside the page. ``AppTest`` runs the
script headlessly and surfaces exceptions, which is the difference between "the
server is up" and "the page works".

Streamlit is an optional extra, so every test here skips when it is absent. CI
installs the pipeline only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="app extras not installed")

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = REPO_ROOT / "app" / "Home.py"


def _run() -> AppTest:
    return AppTest.from_file(str(HOME), default_timeout=30).run()


def test_home_renders_a_friendly_panel_when_the_database_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECTORRADAR_DB_PATH", str(tmp_path / "absent.db"))
    app = _run()

    assert not app.exception, f"Home.py raised: {app.exception}"
    warnings = " ".join(w.value for w in app.warning)
    assert "No database" in warnings
    assert "sectorradar init" in " ".join(m.value for m in app.markdown)


def test_home_reports_zero_companies_on_an_empty_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECTORRADAR_DB_PATH", str(db_path))
    app = _run()

    assert not app.exception, f"Home.py raised: {app.exception}"
    # An initialised but unpopulated database is a normal state, not an error.
    assert app.metric[0].value == "0"


def test_home_shows_counts_for_a_populated_segment(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sectorradar import db

    with db.connect(db_path) as conn:
        db.upsert_segment(conn, "agentic-ai-ch", "Agentic AI CH", "slug: agentic-ai-ch")
        for i, tier in enumerate([1, 1, 2, 3], start=1):
            company_id = db.upsert_company(conn, domain=f"firm{i}.ch", canonical_name=f"Firm {i}")
            db.upsert_membership(
                conn,
                segment_slug="agentic-ai-ch",
                company_id=company_id,
                tier=tier,
                tier_rationale="test",
            )
        conn.commit()

    monkeypatch.setenv("SECTORRADAR_DB_PATH", str(db_path))
    app = _run()

    assert not app.exception, f"Home.py raised: {app.exception}"
    values = [m.value for m in app.metric]
    assert values[0] == "4"  # total
    assert values[1] == "2"  # tier 1
    assert values[2] == "1"  # tier 2
    assert values[3] == "4"  # pending review
