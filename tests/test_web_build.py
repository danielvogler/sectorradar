"""The web build's data contract.

The Astro front end indexes the exported JSON directly, so the shape is the
contract. These tests run without Node: they check the document the build reads
rather than the build itself, which is where a breakage would actually
originate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
#: Whatever segment happens to be exported. Naming one here made the contract
#: tests skip silently for anybody working on a different market.
_EXPORTS = (
    sorted((WEB / "src" / "data").glob("*.web.json")) if (WEB / "src" / "data").exists() else []
)
EXPORT = _EXPORTS[0] if _EXPORTS else WEB / "src" / "data" / "none.web.json"


def test_the_web_project_exists() -> None:
    assert (WEB / "package.json").exists()
    assert (WEB / "src" / "pages" / "index.astro").exists()


#: An asset URL that resolves against the filesystem root rather than the
#: folder it sits in. Two syntaxes, because the bug appeared in both: an
#: attribute in the HTML, and a `url()` in the stylesheet Vite generates for
#: Leaflet's control icons.
ABSOLUTE_ASSET = re.compile(r'\s(?:src|href)="/+(?:\./)*assets/')
ABSOLUTE_CSS_URL = re.compile(r"url\(/+(?:\./)*assets/")


@pytest.mark.skipif(
    not (WEB / "dist" / "index.html").exists(),
    reason="site not built yet — run `make web`",
)
def test_the_build_output_is_relative_so_it_opens_from_the_filesystem() -> None:
    """A folder somebody can open is the point; absolute paths break that.

    This used to assert that `base: './'` appeared in astro.config.mjs, and it
    passed for weeks while every built page carried `/assets/…`. Astro joins
    `base` onto the site root and emits an absolute path anyway — so the test
    was checking that the intention had been written down, not that it had
    worked. Checking the config was the whole mistake.

    The failure it now catches is invisible to whoever builds the site: served
    over http a leading slash is correct, and it breaks only when the folder is
    opened as a file, which is the one thing it exists to do.
    """
    offenders = [
        page.relative_to(REPO).as_posix()
        for page in (WEB / "dist").rglob("*.html")
        if ABSOLUTE_ASSET.search(page.read_text(encoding="utf-8"))
    ]
    offenders += [
        sheet.relative_to(REPO).as_posix()
        for sheet in (WEB / "dist").rglob("*.css")
        if ABSOLUTE_CSS_URL.search(sheet.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"absolute asset paths in {offenders}. The build must run through "
        "web/scripts/relativise.mjs."
    )


def test_the_build_runs_the_step_that_makes_paths_relative() -> None:
    """Astro alone cannot produce relative asset URLs; the extra pass is not optional."""
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    assert "relativise" in package["scripts"]["build"]
    assert (WEB / "scripts" / "relativise.mjs").exists()


def test_generated_data_is_not_committed() -> None:
    """The export is derived from a gitignored database and belongs with it."""
    ignored = (WEB / ".gitignore").read_text(encoding="utf-8")
    assert "src/data/*.json" in ignored
    assert "dist/" in ignored


def test_the_front_end_reads_only_the_export() -> None:
    """No database access from the browser, and no server to run."""
    page = (WEB / "src" / "pages" / "index.astro").read_text(encoding="utf-8")
    for forbidden in ("sqlite", "radar.db", "fetch('http://localhost"):
        assert forbidden not in page


@pytest.mark.skipif(
    not EXPORT.exists(),
    reason="no export generated yet — run `make data`",
)
def test_the_exported_document_matches_what_the_front_end_expects() -> None:
    payload = json.loads(EXPORT.read_text(encoding="utf-8"))

    for key in ("generated_at", "collected_at", "segment", "companies", "analytics"):
        assert key in payload, f"the front end reads {key}"

    assert payload["companies"], "an empty export would render an empty page"

    company = payload["companies"][0]
    for key in (
        "id",
        "domain",
        "canonical_name",
        "tier",
        "lat",
        "lon",
        "is_own",
        "size_band",
        "offerings",
        "case_studies",
        "clients",
        "products",
        "tags",
        "signals",
        # Added later, and each feeds a panel that would simply render empty
        # if it disappeared — the failure mode this test exists to catch.
        "mentions",
        "attributes",
        "traction",
        "seo",
        "geocode_status",
    ):
        assert key in company, f"every company needs {key}"

    for key in (
        "companies",
        "compared",
        "own",
        "by_tier",
        "by_size",
        "by_canton",
        "services",
        "industries",
        "signals",
        "search",
        "stack",
        "totals",
    ):
        assert key in payload["analytics"], f"the statistics panels read {key}"


@pytest.mark.skipif(
    not EXPORT.exists(),
    reason="no export generated yet",
)
def test_no_company_in_the_export_carries_a_claim_without_evidence() -> None:
    """The front end shows a source link beside every claim; it needs one."""
    payload = json.loads(EXPORT.read_text(encoding="utf-8"))
    for company in payload["companies"]:
        for key in ("offerings", "case_studies", "clients", "products"):
            for item in company[key]:
                assert item["evidence_url"], f"{company['domain']} {key} without a URL"
                assert item["evidence_quote"], f"{company['domain']} {key} without a quote"


@pytest.mark.skipif(not EXPORT.exists(), reason="no export generated yet")
def test_every_score_in_the_export_is_inside_its_stated_range() -> None:
    """A score outside 0-100 draws a bar outside its track.

    That is how the industries chart broke: an arithmetic slip produced a
    segment of negative width and split the panel in half. Ranges are cheap to
    assert and the failure is silent without it.
    """
    payload = json.loads(EXPORT.read_text(encoding="utf-8"))

    for company in payload["companies"]:
        traction = company["traction"]
        assert 0 <= traction["points"] <= 100, f"{company['domain']} traction {traction['points']}"
        assert 0.0 <= traction["confidence"] <= 1.0
        assert 0 <= company["seo"]["score"] <= 100, f"{company['domain']} seo"


@pytest.mark.skipif(not EXPORT.exists(), reason="no export generated yet")
def test_no_industry_claims_more_evidence_than_providers() -> None:
    """`providers` is the union, so it cannot be the smaller number."""
    payload = json.loads(EXPORT.read_text(encoding="utf-8"))

    for row in payload["analytics"]["industries"]:
        assert row["providers"] >= row["with_evidence"], row


@pytest.mark.skipif(not EXPORT.exists(), reason="no export generated yet")
def test_aggregates_count_only_companies_in_the_segment() -> None:
    """Rejected candidates are in the document but never in a baseline.

    They were in both, and the canton chart reported 99 companies of unknown
    location against 25 that were actually in the segment.
    """
    payload = json.loads(EXPORT.read_text(encoding="utf-8"))
    tiered = [c for c in payload["companies"] if c["tier"] is not None]
    own = [c for c in tiered if c["is_own"]]

    assert payload["analytics"]["companies"] == len(tiered)
    assert payload["analytics"]["compared"] == len(tiered) - len(own)


@pytest.mark.skipif(not EXPORT.exists(), reason="no export generated yet")
def test_the_segment_definition_travels_with_the_answer() -> None:
    """Figures about a market are uninterpretable without the market's rule."""
    segment = json.loads(EXPORT.read_text(encoding="utf-8"))["segment"]

    for key in ("slug", "name", "inclusion", "tiers", "facets", "queries", "sources_enabled"):
        assert key in segment, f"the method section reads {key}"
    assert segment["inclusion"].strip(), "the inclusion rule is the most important text there is"
    # Never publish which companies belong to whoever ran this.
    assert "own_domains" not in segment


def test_the_build_is_told_which_segment_to_render() -> None:
    """`make serve SEGMENT=x` has to reach the build, not only the export.

    The page globs every exported document and falls back to the first
    alphabetically. With two segments exported, asking for the second one
    exported it correctly and then rendered the first — a wrong answer that
    looks like a working command.
    """
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    for target in ("web:", "web-dev:"):
        line = next(block for block in makefile.split("\n\n") if block.lstrip().startswith(target))
        assert "SECTORRADAR_SEGMENT=$(SEGMENT)" in line, f"{target} does not pass the segment"


def test_the_page_prefers_the_requested_segment_over_the_first_one_found() -> None:
    page = (WEB / "src" / "pages" / "index.astro").read_text(encoding="utf-8")

    assert "SECTORRADAR_SEGMENT" in page
    # The fallback must come second, or the request is ignored whenever more
    # than one document is present — which is exactly when it matters.
    assert page.index("SECTORRADAR_SEGMENT") < page.index("found.at(0)")


def test_the_colleague_command_does_not_force_a_segment() -> None:
    """`make app` runs on a machine that has no segment files and no database.

    Forcing the default meant somebody whose bucket holds one market was told
    there was "no published data" for a different market they had never heard
    of. With no segment, `pull` lists what is actually there.
    """
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    app = next(block for block in makefile.split("\n\n") if block.lstrip().startswith("app:"))

    assert "sectorradar pull" in app
    assert "origin SEGMENT" in app, "app must pass --segment only when one was named"


@pytest.mark.skipif(
    not (WEB / "dist" / "index.html").exists(),
    reason="site not built yet — run `make web`",
)
def test_the_build_ships_nothing_the_page_does_not_use() -> None:
    """A bucket somebody was sent a link to should not hold build leftovers.

    Naming chunks explicitly once split the bundle and emitted two JavaScript
    files nothing referenced, including Astro's server runtime.
    """
    dist = WEB / "dist"
    text = "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in dist.rglob("*")
        if p.is_file() and p.suffix in {".html", ".css", ".js"}
    )

    orphans = [
        p.relative_to(dist).as_posix()
        for p in dist.rglob("*")
        if p.is_file() and p.name != "index.html" and p.name not in text
    ]
    assert not orphans, f"built but never referenced: {orphans}"
