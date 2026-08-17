"""LLM extraction, and the evidence check that makes its output trustworthy.

A model reading generic marketing copy will confidently produce a plausible
service list the page does not support, and the failure is silent: a fabricated
profile looks exactly like a good one. Persuasion in the prompt helps but does
not settle it.

So the defence is mechanical. Every quote the model returns must be found
verbatim in the text it was given, compared with whitespace and case
normalised. A claim whose quote cannot be found is dropped, and the drop rate
is reported — a rising ``hallucination_rate`` is the signal that a prompt or a
model change has made things worse.

Prompts are versioned and the ``extractor`` column records
``<prompt-version>/<model-id>``, so a re-run with a new prompt is
distinguishable from the old one rather than silently overwriting it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sectorradar import db, fetch, swiss
from sectorradar.config import Segment, Settings
from sectorradar.llm import LLMClient, Usage
from sectorradar.logging import get_logger
from sectorradar.models import CompanyProfile, Offering

log = get_logger(__name__)

PROMPT_VERSION = "extract.v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"

#: How much page text to send. Enough for a small firm's whole site, bounded so
#: one sprawling site cannot dominate a run's cost.
MAX_CHARS_PER_PAGE = 6000
MAX_TOTAL_CHARS = 24000

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s()/.-]{7,}\d")


@dataclass
class ExtractReport:
    companies: int = 0
    profiles: int = 0
    failed: int = 0
    offerings_kept: int = 0
    offerings_dropped: int = 0
    usage: Usage = field(default_factory=Usage)

    @property
    def hallucination_rate(self) -> float:
        return hallucination_rate(
            dropped=self.offerings_dropped, total=self.offerings_kept + self.offerings_dropped
        )


# ---------------------------------------------------------------------------
# The evidence check
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Collapse whitespace and case, so reflowing is not mistaken for invention."""
    return " ".join(text.split()).casefold()


def quote_is_supported(quote: str, page_text: str) -> bool:
    """Whether ``quote`` genuinely appears in ``page_text``.

    Whitespace and case are normalised on both sides: a model that reflows a
    line has not fabricated anything. Everything else must match exactly, which
    is what stops two distant fragments being stitched into a claim the page
    never made.
    """
    if not quote or not quote.strip():
        return False
    return _normalise(quote) in _normalise(page_text)


def contains_personal_data(text: str) -> bool:
    """Whether a span carries something about an identifiable individual."""
    return bool(EMAIL_RE.search(text) or PHONE_RE.search(text))


def hallucination_rate(*, dropped: int, total: int) -> float:
    return dropped / total if total else 0.0


def drop_unsupported(profile: CompanyProfile, pages: dict[str, str]) -> tuple[CompanyProfile, int]:
    """Remove every offering whose evidence does not hold up.

    Two ways to fail: the quote is not in the cited page, or the cited page was
    never fetched at all. Both mean the same thing — there is nothing behind the
    claim — so both are dropped and counted.
    """
    kept: list[Offering] = []
    dropped = 0

    for offering in profile.offerings:
        page_text = pages.get(offering.evidence_url)
        if page_text is None:
            log.debug("extract.dropped_unknown_url", url=offering.evidence_url)
            dropped += 1
            continue
        if not quote_is_supported(offering.evidence_quote, page_text):
            log.debug("extract.dropped_unsupported", quote=offering.evidence_quote[:60])
            dropped += 1
            continue
        if contains_personal_data(offering.evidence_quote):
            log.debug("extract.dropped_personal_data")
            dropped += 1
            continue
        kept.append(offering)

    return profile.model_copy(update={"offerings": tuple(kept)}), dropped


def strip_personal_data(profile: CompanyProfile) -> CompanyProfile:
    """Scrub contact details from free text.

    The prompt forbids collecting these, and the model generally complies. This
    is the second layer, because "generally" is not a basis for a legal
    constraint.
    """
    cleaned = EMAIL_RE.sub("[redacted]", profile.one_liner)
    cleaned = PHONE_RE.sub("[redacted]", cleaned)
    return profile.model_copy(update={"one_liner": " ".join(cleaned.split())})


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


def build_prompt(domain: str, pages: dict[str, str], segment: Segment) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")

    facet_lines = (
        "\n".join(f"- `{facet}`: {', '.join(values)}" for facet, values in segment.facets.items())
        or "- (no controlled facets for this segment)"
    )

    blocks: list[str] = []
    budget = MAX_TOTAL_CHARS
    for url, text in pages.items():
        if budget <= 0:
            break
        excerpt = text[: min(MAX_CHARS_PER_PAGE, budget)]
        budget -= len(excerpt)
        blocks.append(f"### {url}\n\n{excerpt}")

    return template.format(domain=domain, facets=facet_lines, pages="\n\n".join(blocks))


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _companies_with_pages(conn: sqlite3.Connection, segment: Segment) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT c.id, c.domain
          FROM company c
          JOIN membership m ON m.company_id = c.id
          JOIN page p       ON p.company_id = c.id
         WHERE m.segment_slug = ?
           AND COALESCE(m.review_state, 'pending') != 'rejected'
      ORDER BY c.id
        """,
        (segment.slug,),
    ).fetchall()


def _pages_signature(conn: sqlite3.Connection, company_id: int) -> str:
    rows = conn.execute(
        "SELECT content_sha FROM page WHERE company_id = ? ORDER BY url_sha", (company_id,)
    ).fetchall()
    return "|".join(str(r["content_sha"] or "") for r in rows)


def _last_signature(conn: sqlite3.Connection, company_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT value FROM company_field
         WHERE company_id = ? AND field = '_pages_signature'
      ORDER BY extracted_at DESC LIMIT 1
        """,
        (company_id,),
    ).fetchone()
    return str(row["value"]) if row else None


def _record_field(
    conn: sqlite3.Connection,
    company_id: int,
    field_name: str,
    value: object,
    source_url: str,
    extractor: str,
    *,
    quote: str | None = None,
    confidence: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO company_field
          (company_id, field, value, source_url, evidence_quote, confidence, extractor, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (company_id, field_name, str(value), source_url, quote, confidence, extractor, _now()),
    )


def extract(
    conn: sqlite3.Connection,
    segment: Segment,
    settings: Settings,
    client: LLMClient,
    *,
    only_changed: bool = False,
    dry_run: bool = False,
) -> ExtractReport:
    """Build an evidence-carrying profile for every fetched company."""
    report = ExtractReport()
    extractor = f"{PROMPT_VERSION}/{client.model}"

    for row in _companies_with_pages(conn, segment):
        company_id, domain = int(row["id"]), str(row["domain"])
        pages = dict(fetch.page_texts(conn, company_id, settings.raw_dir))
        if not pages:
            continue

        signature = _pages_signature(conn, company_id)
        if only_changed and _last_signature(conn, company_id) == signature:
            log.debug("extract.unchanged", domain=domain)
            continue

        report.companies += 1
        result = client.structured(build_prompt(domain, pages, segment), CompanyProfile)
        report.usage = report.usage + result.usage

        if result.value is None:
            report.failed += 1
            log.warning("extract.no_profile", domain=domain)
            continue

        profile, dropped = drop_unsupported(strip_personal_data(result.value), pages)
        report.profiles += 1
        report.offerings_kept += len(profile.offerings)
        report.offerings_dropped += dropped

        if dry_run:
            continue

        primary_url = next(iter(pages))
        conn.execute("DELETE FROM offering WHERE company_id = ?", (company_id,))
        for offering in profile.offerings:
            conn.execute(
                """
                INSERT INTO offering
                  (company_id, label, evidence_url, evidence_quote, extracted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    offering.label,
                    offering.evidence_url,
                    offering.evidence_quote,
                    _now(),
                ),
            )

        db.upsert_company(
            conn,
            domain=domain,
            canonical_name=profile.legal_name or domain,
            one_liner=profile.one_liner or None,
            legal_name=profile.legal_name,
            headcount_est=profile.headcount_estimate,
            founded_year=profile.founded_year,
            city=swiss.canonical_city(profile.city),
            # Folded to a two-letter code, or dropped. A model answers this
            # field with whatever the site says — four languages, two
            # registers, and occasionally somewhere that is not Swiss at all.
            canton=swiss.canton_code(profile.canton),
            languages=",".join(profile.languages) or None,
            last_enriched=_now(),
        )

        for name, value in (
            ("headcount_est", profile.headcount_estimate),
            ("founded_year", profile.founded_year),
            ("city", swiss.canonical_city(profile.city)),
            ("canton", swiss.canton_code(profile.canton)),
        ):
            if value is not None:
                _record_field(
                    conn,
                    company_id,
                    name,
                    value,
                    primary_url,
                    extractor,
                    confidence=profile.confidence,
                )

        # Stored as a field so `only_changed` can tell whether anything moved.
        _record_field(conn, company_id, "_pages_signature", signature, primary_url, extractor)

        # Commit per company. Extraction is one paid LLM call each and runs for
        # minutes over a segment; committing once at the end would mean an
        # interrupt throws away work that has already been paid for.
        conn.commit()

        for facet, values in profile.facets.items():
            for value in values:
                conn.execute(
                    """
                    INSERT INTO tag (company_id, facet, value, confidence, source_url)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, facet, value) DO UPDATE SET
                      confidence = excluded.confidence
                    """,
                    (company_id, facet, value, profile.confidence, primary_url),
                )

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    log.info(
        "extract.done",
        segment=segment.slug,
        companies=report.companies,
        kept=report.offerings_kept,
        dropped=report.offerings_dropped,
        hallucination_rate=round(report.hallucination_rate, 3),
        cost_usd=round(report.usage.cost_usd, 4),
    )
    return report
