"""The import boundary between the pipeline and the app.

``data/radar.db`` is the only interface between ``src/sectorradar/`` and
``app/``. Streamlit re-runs the whole script on every widget interaction, so if
anything expensive leaks across that line the app becomes unusable at a few
hundred rows.

This walks the AST rather than grepping: a grep for "import streamlit" is
defeated by an aliased or conditional import, and produces false positives on
the word appearing in a docstring or comment.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "sectorradar"
APP = REPO_ROOT / "app"


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    """Every top-level module name imported by a file, however it is spelled.

    Covers `import x`, `import x.y`, `import x as z`, `from x import y` and
    `from x.y import z`. Relative imports have no top-level name and are
    ignored deliberately.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_pipeline_never_imports_streamlit(path: Path) -> None:
    imported = _imported_modules(path)
    offenders = imported & {"streamlit", "pydeck"}
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(offenders)}. "
        "The pipeline must not depend on the app layer — the app extras are "
        "deliberately not a runtime dependency."
    )


@pytest.mark.skipif(not APP.exists(), reason="app/ not created yet")
@pytest.mark.parametrize(
    "path",
    _python_files(APP) or [APP],
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_app_never_imports_the_pipeline(path: Path) -> None:
    if path == APP:  # no app files yet
        pytest.skip("no python files under app/ yet")
    imported = _imported_modules(path)
    assert "sectorradar" not in imported, (
        f"{path.relative_to(REPO_ROOT)} imports sectorradar. The app reads the "
        "SQLite file and nothing else; importing the pipeline lets a crawl or an "
        "LLM call happen on a Streamlit rerun."
    )


def test_all_sql_lives_in_the_queries_module() -> None:
    """Pages hold no SQL. Keeping it in one module is what makes it reviewable."""
    pages = [p for p in _python_files(APP) if p.name != "queries.py"] if APP.exists() else []
    if not pages:
        pytest.skip("no app pages yet")

    keywords = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE")
    offenders: list[str] = []
    for path in pages:
        text = path.read_text(encoding="utf-8").upper()
        if any(k in text for k in keywords):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"SQL found outside app/lib/queries.py: {offenders}"
