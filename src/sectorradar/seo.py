"""Why some of these sites turn up in a search and others do not.

Every other part of this dataset records what a company *says*. This part
records what its markup *does*, which is a different kind of fact: it is in the
HTML, it has a right answer, and it costs nothing to recompute. No language
model is involved, and none should be — "does this site publish Organization
schema" is not a question anybody should be guessing at.

What it measures is the deterministic half of search visibility: the things a
site controls in its own `<head>` and body. Title and description, canonical
and hreflang, structured data, heading structure, image alt text, internal
linking, content depth, and whether the site is telling crawlers to stay away.

**What it deliberately does not measure**: backlinks, domain authority, actual
rankings, or traffic. Those need a third-party index this tool does not have,
and inventing a proxy for them would produce a number that looks like the thing
without being it. What is here is the part you can act on by editing your own
site, which is also the part worth comparing against competitors.

Two rules, the same ones the traction score follows:

* A site nobody crawled is **unknown**, not bad.
* Every deduction comes with a finding that names what to fix, because a score
  on its own tells you where you stand and nothing about what to do.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Final

from selectolax.parser import HTMLParser

#: Google truncates around here. Outside the range is not fatal, but a title of
#: four words is leaving the single strongest on-page signal half unused.
TITLE_MIN: Final = 20
TITLE_MAX: Final = 65
DESCRIPTION_MIN: Final = 70
DESCRIPTION_MAX: Final = 165

#: A page thinner than this is rarely what a search engine chooses to show for
#: a competitive query, whatever else it does right.
THIN_CONTENT_WORDS: Final = 150

#: Weights sum to 100. As with traction, they are a judgement, which is why the
#: findings travel with the number.
WEIGHTS: Final[dict[str, float]] = {
    "title": 14.0,
    "description": 12.0,
    "structured_data": 16.0,
    "headings": 10.0,
    "content_depth": 14.0,
    "multilingual": 10.0,
    "canonical": 6.0,
    "social": 6.0,
    "images": 6.0,
    "mobile": 6.0,
}

_WORD = re.compile(r"\w+", re.UNICODE)
_NOISE = ("script", "style", "noscript", "svg")


@dataclass
class SeoProfile:
    """The measurable half of how findable a site is."""

    pages_analysed: int = 0

    has_title: bool = False
    title_length: int = 0
    has_description: bool = False
    description_length: int = 0
    has_canonical: bool = False
    has_hreflang: bool = False
    languages_declared: int = 0
    has_open_graph: bool = False
    has_viewport: bool = False
    blocks_indexing: bool = False

    schema_types: tuple[str, ...] = ()
    single_h1: bool = False
    heading_levels: int = 0
    median_word_count: int = 0
    image_alt_ratio: float = 1.0
    internal_links_median: int = 0

    score: int = 0
    findings: tuple[str, ...] = ()
    components: dict[str, float] = field(default_factory=dict)

    @property
    def is_unknown(self) -> bool:
        """No pages were analysed, so nothing is known — good or bad."""
        return self.pages_analysed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_analysed": self.pages_analysed,
            "score": self.score,
            "is_unknown": self.is_unknown,
            "findings": list(self.findings),
            "components": {k: round(v, 1) for k, v in self.components.items()},
            "has_title": self.has_title,
            "title_length": self.title_length,
            "has_description": self.has_description,
            "description_length": self.description_length,
            "has_canonical": self.has_canonical,
            "has_hreflang": self.has_hreflang,
            "languages_declared": self.languages_declared,
            "has_open_graph": self.has_open_graph,
            "has_viewport": self.has_viewport,
            "blocks_indexing": self.blocks_indexing,
            "schema_types": list(self.schema_types),
            "single_h1": self.single_h1,
            "median_word_count": self.median_word_count,
            "image_alt_ratio": round(self.image_alt_ratio, 2),
            "internal_links_median": self.internal_links_median,
        }


def _attr(tree: HTMLParser, selector: str, name: str) -> str:
    node = tree.css_first(selector)
    if node is None:
        return ""
    return (node.attributes.get(name) or "").strip()


def _schema_types(tree: HTMLParser) -> set[str]:
    """Every schema.org @type declared in JSON-LD on the page.

    Malformed JSON is ignored rather than raised: a broken structured-data
    block is a finding about that site, not a reason to abandon the analysis of
    everything else on it.
    """
    found: set[str] = set()
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except (ValueError, TypeError):
            continue
        stack: list[Any] = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                declared = item.get("@type")
                if isinstance(declared, str):
                    found.add(declared)
                elif isinstance(declared, list):
                    found.update(str(t) for t in declared)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def _visible_words(tree: HTMLParser) -> int:
    for tag in _NOISE:
        for node in tree.css(tag):
            node.decompose()
    body = tree.body
    text = body.text(separator=" ", strip=True) if body else ""
    return len(_WORD.findall(text))


def _analyse_page(html: str) -> dict[str, Any]:
    tree = HTMLParser(html)

    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""
    description = _attr(tree, 'meta[name="description"]', "content")
    robots = _attr(tree, 'meta[name="robots"]', "content").lower()

    hreflangs = {
        (node.attributes.get("hreflang") or "").split("-")[0].lower()
        for node in tree.css("link[rel=alternate][hreflang]")
    }
    hreflangs.discard("")
    hreflangs.discard("x-default")

    images = tree.css("img")
    with_alt = sum(1 for img in images if (img.attributes.get("alt") or "").strip())

    return {
        "title": title,
        "description": description,
        "canonical": bool(tree.css_first("link[rel=canonical]")),
        "hreflangs": hreflangs,
        "open_graph": bool(tree.css_first('meta[property^="og:"]')),
        "viewport": bool(tree.css_first('meta[name="viewport"]')),
        "noindex": "noindex" in robots,
        "schema": _schema_types(tree),
        "h1": len(tree.css("h1")),
        "levels": sum(1 for level in ("h1", "h2", "h3", "h4") if tree.css_first(level)),
        "images": len(images),
        "images_with_alt": with_alt,
        "internal_links": len([a for a in tree.css("a[href]") if _is_internal(a.attributes)]),
        "words": _visible_words(HTMLParser(html)),
    }


def _is_internal(attributes: dict[str, str | None]) -> bool:
    href = (attributes.get("href") or "").strip()
    return bool(href) and not href.startswith(("http://", "https://", "mailto:", "tel:", "#"))


def _band(value: float, low: float, high: float, weight: float) -> float:
    """Full marks inside a range, partial credit outside it, never negative."""
    if value <= 0:
        return 0.0
    if low <= value <= high:
        return weight
    distance = (low - value) if value < low else (value - high)
    return max(0.0, weight * (1 - distance / max(low, 1)))


def analyse(pages: dict[str, str]) -> SeoProfile:
    """Measure a company's site from the HTML already on disk.

    ``pages`` maps URL to raw HTML. The home page carries the head-level
    signals — title, description, canonical, schema — because that is the page
    a search engine and a human both land on first; body-level measures are
    taken across every page and reported as medians, so one unusually long
    article does not make a thin site look deep.
    """
    if not pages:
        return SeoProfile()

    analysed = [_analyse_page(html) for html in pages.values()]
    # The shortest URL is the closest thing to a home page without having to
    # know the domain: "/" beats "/de/leistungen/ki-agenten".
    home = analysed[min(range(len(pages)), key=lambda i: len(list(pages)[i]))]

    languages = set[str]()
    for page in analysed:
        languages |= page["hreflangs"]

    schema: set[str] = set()
    for page in analysed:
        schema |= page["schema"]

    total_images = sum(p["images"] for p in analysed)
    images_with_alt = sum(p["images_with_alt"] for p in analysed)

    profile = SeoProfile(
        pages_analysed=len(pages),
        has_title=bool(home["title"]),
        title_length=len(home["title"]),
        has_description=bool(home["description"]),
        description_length=len(home["description"]),
        has_canonical=any(p["canonical"] for p in analysed),
        has_hreflang=bool(languages),
        languages_declared=len(languages),
        has_open_graph=any(p["open_graph"] for p in analysed),
        has_viewport=any(p["viewport"] for p in analysed),
        # The home page only. A `noindex` on a thank-you page or a print view
        # is ordinary housekeeping; flagging a site for it said 38 companies
        # had opted out of search when almost none had.
        blocks_indexing=bool(home["noindex"]),
        schema_types=tuple(sorted(schema)),
        single_h1=home["h1"] == 1,
        heading_levels=home["levels"],
        median_word_count=int(statistics.median(p["words"] for p in analysed)),
        # No images is not a failure to caption images. Scoring it as zero
        # would punish a text-only site for something it never did wrong.
        image_alt_ratio=(images_with_alt / total_images) if total_images else 1.0,
        internal_links_median=int(statistics.median(p["internal_links"] for p in analysed)),
    )
    return _score(profile)


def _score(profile: SeoProfile) -> SeoProfile:
    components: dict[str, float] = {}
    findings: list[str] = []

    components["title"] = _band(profile.title_length, TITLE_MIN, TITLE_MAX, WEIGHTS["title"])
    if not profile.has_title:
        findings.append(
            "The home page has no <title>. It is the strongest on-page signal there is."
        )
    elif profile.title_length < TITLE_MIN:
        findings.append(
            f"The title is {profile.title_length} characters. There is room for "
            "the service and the region, which is what people actually search for."
        )
    elif profile.title_length > TITLE_MAX:
        findings.append(f"The title is {profile.title_length} characters and will be truncated.")

    components["description"] = _band(
        profile.description_length, DESCRIPTION_MIN, DESCRIPTION_MAX, WEIGHTS["description"]
    )
    if not profile.has_description:
        findings.append(
            "No meta description. The search result then quotes whatever text "
            "the page starts with, which is rarely the sentence you would pick."
        )
    elif not DESCRIPTION_MIN <= profile.description_length <= DESCRIPTION_MAX:
        findings.append(
            f"The meta description is {profile.description_length} characters; "
            f"{DESCRIPTION_MIN}-{DESCRIPTION_MAX} is what gets shown in full."
        )

    # Structured data is the biggest single differentiator between sites that
    # get a rich result and sites that get a blue link.
    valuable = {"Organization", "LocalBusiness", "ProfessionalService", "FAQPage", "Article"}
    matched = valuable & set(profile.schema_types)
    components["structured_data"] = min(len(matched), 3) / 3 * WEIGHTS["structured_data"]
    if not profile.schema_types:
        findings.append(
            "No structured data. Organization or LocalBusiness markup is what "
            "makes a search engine show your address, logo and links rather "
            "than a plain result."
        )
    elif "FAQPage" not in profile.schema_types:
        findings.append(
            "No FAQPage markup. Question-and-answer pages are how a site wins "
            "the answer box for 'what does an AI agent cost' style queries."
        )

    components["headings"] = WEIGHTS["headings"] * (
        (0.5 if profile.single_h1 else 0.0) + 0.5 * min(profile.heading_levels, 3) / 3
    )
    if not profile.single_h1:
        findings.append("The home page does not have exactly one <h1>.")

    components["content_depth"] = min(
        WEIGHTS["content_depth"],
        profile.median_word_count / THIN_CONTENT_WORDS * WEIGHTS["content_depth"],
    )
    if profile.median_word_count < THIN_CONTENT_WORDS:
        findings.append(
            f"Pages carry a median of {profile.median_word_count} words. Thin pages "
            "rarely rank for anything competitive, whatever else is right."
        )

    components["multilingual"] = min(profile.languages_declared, 3) / 3 * WEIGHTS["multilingual"]
    if not profile.has_hreflang:
        findings.append(
            "No hreflang. In a country that searches in three languages, an "
            "untagged translation competes with its own other-language pages."
        )

    components["canonical"] = WEIGHTS["canonical"] if profile.has_canonical else 0.0
    if not profile.has_canonical:
        findings.append("No canonical URL, so duplicate paths compete with each other.")

    components["social"] = WEIGHTS["social"] if profile.has_open_graph else 0.0
    if not profile.has_open_graph:
        findings.append("No Open Graph tags, so shared links render as bare URLs.")

    components["images"] = profile.image_alt_ratio * WEIGHTS["images"]
    if profile.image_alt_ratio < 0.8:
        findings.append(
            f"Only {profile.image_alt_ratio:.0%} of images have alt text — "
            "an accessibility problem before it is a search one."
        )

    components["mobile"] = WEIGHTS["mobile"] if profile.has_viewport else 0.0
    if not profile.has_viewport:
        findings.append("No viewport meta tag, so the site is not mobile-ready.")

    # Nothing else matters if the site is asking not to be indexed. Say so
    # first and loudest rather than burying it under a middling score.
    if profile.blocks_indexing:
        findings.insert(0, "The home page carries noindex. It cannot appear in search at all.")

    profile.components = components
    profile.findings = tuple(findings)
    profile.score = round(sum(components.values()))
    return profile


# --------------------------------------------------------------------------
# The stage
# --------------------------------------------------------------------------


@dataclass
class SeoReport:
    companies: int = 0
    skipped: int = 0
    blocking_indexing: int = 0
    median_score: int = 0


def analyse_segment(conn: Any, segment: Any) -> SeoReport:
    """Recompute every company's search profile from stored markup.

    Deliberately a full recompute rather than an incremental one: it reads
    files already on disk, calls nothing, and finishes in seconds, so the
    machinery required to work out what changed would cost more than the work
    it saved.
    """
    from sectorradar import fetch

    report = SeoReport()
    scores: list[int] = []
    now = (
        __import__("datetime")
        .datetime.now(__import__("datetime").UTC)
        .isoformat(timespec="seconds")
    )

    rows = conn.execute(
        """
        SELECT c.id, c.domain FROM company c
          JOIN membership m ON m.company_id = c.id
         WHERE m.segment_slug = ?
        """,
        (segment.slug,),
    ).fetchall()

    for row in rows:
        company_id = int(row["id"])
        pages = dict(fetch.page_html(conn, company_id))
        if not pages:
            report.skipped += 1
            continue

        profile = analyse(pages)
        report.companies += 1
        scores.append(profile.score)
        report.blocking_indexing += profile.blocks_indexing

        conn.execute(
            """
            INSERT OR REPLACE INTO seo_profile
              (company_id, pages_analysed, score, title_length, description_length,
               has_canonical, has_hreflang, languages_declared, has_open_graph,
               has_viewport, blocks_indexing, schema_types, single_h1,
               median_word_count, image_alt_ratio, internal_links_med,
               findings, components, analysed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                profile.pages_analysed,
                profile.score,
                profile.title_length,
                profile.description_length,
                int(profile.has_canonical),
                int(profile.has_hreflang),
                profile.languages_declared,
                int(profile.has_open_graph),
                int(profile.has_viewport),
                int(profile.blocks_indexing),
                json.dumps(list(profile.schema_types)),
                int(profile.single_h1),
                profile.median_word_count,
                profile.image_alt_ratio,
                profile.internal_links_median,
                json.dumps(list(profile.findings)),
                json.dumps(profile.components),
                now,
            ),
        )
        conn.commit()

    report.median_score = int(statistics.median(scores)) if scores else 0
    return report
