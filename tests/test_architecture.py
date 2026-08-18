"""The boundary between the pipeline and the thing that displays it.

There is one interface: the pipeline writes ``data/radar.db`` and exports a
single JSON document from it; the front end reads that document. Nothing else
crosses. That is what makes ``web/dist/`` a folder you can hand to somebody —
it has no database, no Python, and no network call to anything you control.

The rules below are worth enforcing mechanically because both directions fail
quietly. A front end that reaches for the database still works on the machine
that has one, and only breaks for the colleague you sent it to. A pipeline that
imports the view layer still runs, and only breaks when the view is not
installed.

These walk the AST rather than grepping: a grep for "import streamlit" is
defeated by an aliased or conditional import, and produces false positives on
the word appearing in a docstring.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "sectorradar"
WEB_SRC = REPO_ROOT / "web" / "src"

#: View-layer packages the pipeline must never depend on. `streamlit` and
#: `pydeck` are named because they were once a second front end here, and the
#: cheapest way to end up maintaining two again is to let an import back in.
VIEW_PACKAGES = frozenset({"streamlit", "pydeck", "folium"})

#: Patterns for "this file talks to a database". They are anchored rather than
#: substring matches on purpose: a bare search for ``SELECT`` hits every
#: ``<select>`` element on the page, and one for ``UPDATE`` hits the sentence
#: "the map and the list update together". A check that cries wolf on its own
#: markup gets deleted the first time it blocks a commit.
DATA_ACCESS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a database file", re.compile(r"radar\.db|\.sqlite3?\b", re.IGNORECASE)),
    ("a sqlite driver", re.compile(r"\b(sqlite3|better-sqlite3|sql\.js)\b", re.IGNORECASE)),
    ("a SELECT query", re.compile(r"\bSELECT\b[\s\S]{0,300}?\bFROM\b")),
    ("a write query", re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b")),
)


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _web_sources() -> list[Path]:
    return sorted(
        p
        for p in WEB_SRC.rglob("*")
        if p.suffix in {".ts", ".astro"} and "node_modules" not in p.parts
    )


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
def test_pipeline_never_imports_a_view_package(path: Path) -> None:
    imported = _imported_modules(path)
    offenders = imported & VIEW_PACKAGES
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(offenders)}. The pipeline "
        "must not depend on the view layer: it renders nothing, and a view "
        "dependency here is how a second front end starts."
    )


@pytest.mark.parametrize("path", _web_sources(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_the_front_end_never_reaches_for_the_database(path: Path) -> None:
    """The site reads the exported JSON and nothing else.

    A `.db` path or a SQL keyword in here means the built output only works on
    a machine that has the database — which is the one property the static
    build exists to avoid.
    """
    text = path.read_text(encoding="utf-8")
    offenders = [name for name, pattern in DATA_ACCESS if pattern.search(text)]
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} mentions {offenders}. The front end reads "
        "the exported JSON document; anything else breaks the moment the folder "
        "is opened on a machine without the pipeline."
    )


@pytest.mark.parametrize("path", _web_sources(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_the_front_end_calls_no_network_service(path: Path) -> None:
    """No fetch, no API host, no phoning home.

    Map tiles are the deliberate exception — a basemap has to come from
    somewhere, and it degrades to a blank map rather than to a broken page.
    """
    text = path.read_text(encoding="utf-8")
    for call in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource"):
        assert call not in text, (
            f"{path.relative_to(REPO_ROOT)} calls {call}. The page is a static file; "
            "every number on it is baked in at build time on purpose."
        )


def test_the_site_names_no_particular_segment() -> None:
    """The front end must not know which market it is displaying.

    A segment is one YAML file. When the page imported
    `../data/<slug>.web.json` by name, pointing the tool at a different
    market meant editing the front end — which defeats the premise. It now
    globs whatever was exported, so a new segment needs no code change at all.
    """
    named = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _web_sources()
        if re.search(r"""['"]\.\./data/[a-z0-9-]+\.web\.json['"]""", path.read_text("utf-8"))
    ]
    assert not named, (
        f"{named} import a named segment document. Use import.meta.glob so the "
        "page renders whichever segment was exported."
    )


def test_the_site_reads_its_data_from_one_place() -> None:
    """One glob, so there is one thing to publish and one thing to go stale."""
    globs: set[str] = set()
    for path in _web_sources():
        globs.update(
            re.findall(r"""import\.meta\.glob<[^>]*>\(['"]([^'"]+)['"]""", path.read_text("utf-8"))
        )
    assert globs == {"../data/*.web.json"}, (
        f"expected exactly one data glob, found {sorted(globs)}. "
        "Two sources can disagree about what is true."
    )


# --- what is allowed to be committed ----------------------------------------

#: Paths that must never enter version control, with why. Each is derived from
#: something else — regenerate it, do not store it — or is nobody's business
#: but the operator's.
NEVER_TRACKED: tuple[tuple[str, str], ...] = (
    ("data/", "collected pages and the database; see DATA.md"),
    ("web/src/data/", "the exported document, regenerated by `make data`"),
    ("web/dist/", "the built site, regenerated by `make web`"),
    ("segments/private/", "markets somebody would rather not announce mapping"),
    ("notes/", "working material: build log, spend, the original brief"),
    (".env", "credentials"),
)


@pytest.mark.parametrize(("path", "why"), NEVER_TRACKED, ids=lambda v: v if "/" in str(v) else "")
def test_nothing_under_these_paths_is_tracked(path: str, why: str) -> None:
    """A question somebody had to ask by hand, answered mechanically instead.

    Every one of these is either derived — regenerate it rather than store it —
    or private. The export is the interesting case: it was only ignored because
    an unanchored `data/` rule happened to match `web/src/data/`, which also
    meant the deliberate rule in web/.gitignore was never consulted.
    """
    tracked = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        ["git", "ls-files", "--", path],  # noqa: S607 - git resolved from PATH deliberately
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()

    assert not tracked, f"{path} is in version control ({why}): {tracked[:5]}"


def test_a_local_segment_overlay_is_never_tracked() -> None:
    """Overlays carry which companies in a market are the operator's own."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", "segments/*.local.yaml"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()

    assert not tracked, f"a local overlay is in version control: {tracked}"


def test_every_shipped_segment_is_one_somebody_would_publish() -> None:
    """The public examples exist to be read, so they must stay readable ones."""
    shipped = sorted(p.stem for p in (REPO_ROOT / "segments").glob("*.yaml"))

    assert shipped, "no example segment ships, so nobody can see what one looks like"
    for slug in shipped:
        assert not slug.endswith(".local"), slug


def test_every_source_file_the_build_needs_is_tracked() -> None:
    """A clone must be able to build the page it ships.

    `web/src/lib/app.ts` — the entire front-end runtime — was untracked for the
    life of the project, because the Python gitignore template's unanchored
    `lib/` matches a directory of that name at any depth. Nothing caught it:
    the working tree had the file, so every local build worked, and only a
    build from a fresh clone failed. The same trap had already taken
    `web/src/data/` through a bare `data/`.
    """
    sources = [
        path
        for path in (REPO_ROOT / "web" / "src").rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".astro", ".css"}
        and "node_modules" not in path.parts
    ]
    assert sources, "no front-end sources found at all"

    tracked = set(
        subprocess.run(
            ["git", "ls-files", "--", "web/src"],  # noqa: S607 - git from PATH, deliberately
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
    )

    missing = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in sources
        if p.relative_to(REPO_ROOT).as_posix() not in tracked
    )
    assert not missing, (
        f"these are on disk but not in version control, so a clone cannot build: {missing}"
    )


def test_the_packaging_ignores_are_anchored_to_the_root() -> None:
    """An unanchored rule matches at any depth, which is how source vanishes.

    `lib/` was meant for Python build output. It also matched `web/src/lib/`.
    """
    rules = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dangerous = {"lib/", "lib64/", "build/", "dist/", "data/", "var/", "share/", "bin/", "include/"}

    unanchored = [line for line in rules if line.strip() in dangerous]
    assert not unanchored, (
        f"anchor these to the repository root as /{unanchored[0] if unanchored else ''}: "
        f"{unanchored} — unanchored, they match a directory of that name at any depth"
    )
