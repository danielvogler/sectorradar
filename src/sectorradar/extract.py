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
from typing import Final
from urllib.parse import urlparse

from sectorradar import db, fetch, swiss
from sectorradar.config import Segment, Settings
from sectorradar.industries import canonical_industry
from sectorradar.llm import LLMClient, Usage
from sectorradar.logging import get_logger
from sectorradar.models import SITE_SIGNALS, CompanyProfile, MediaMention, Offering

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
    case_studies: int = 0
    clients: int = 0
    products: int = 0
    mentions: int = 0
    signals: int = 0
    depth_dropped: int = 0
    attributes: int = 0
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


#: Shapes that look like a person rather than an organisation. A sole trader's
#: company name is their name, and this tool does not record people.
_PERSON_LIKE = re.compile(r"^(dr|prof|herr|frau|mr|mrs|ms|monsieur|madame)\.?\s", re.IGNORECASE)


def looks_like_a_person(name: str) -> bool:
    """Whether a client name is an individual rather than an organisation.

    Deliberately conservative: two capitalised words with no legal form and no
    organisational word is far more likely to be a person than a company, and
    dropping a real company is a smaller harm than storing a real person.
    """
    cleaned = name.strip()
    if _PERSON_LIKE.match(cleaned):
        return True

    words = cleaned.split()
    if len(words) != 2:
        return False

    # A two-letter token is not a given name or a surname. "Swiss Re" was read
    # as a person on the two-capitalised-words rule alone, which is how a large
    # insurer got mistaken for an individual.
    if any(len(w) < 3 for w in words):
        return False

    # Geographic and brand adjectives that open a company name far more often
    # than they open a person's.
    corporate_openers = (
        "swiss",
        "suisse",
        "schweizer",
        "schweizerische",
        "svizzera",
        "helvetia",
        "basler",
        "zurcher",
        "zürcher",
        "berner",
        "alpine",
        "global",
        "united",
        "royal",
        "national",
        "federal",
        "european",
        "nordic",
        "digital",
        "smart",
    )
    if words[0].casefold() in corporate_openers:
        return False

    organisational = (
        "ag",
        "gmbh",
        "sa",
        "sarl",
        "sagl",
        "ltd",
        "inc",
        "group",
        "bank",
        "insurance",
        "versicherung",
        "holding",
        "technologies",
        "solutions",
        "systems",
        "services",
        "consulting",
        "software",
        "labs",
        "media",
        "university",
        "universität",
        "hochschule",
        "klinik",
        "spital",
    )
    lowered = cleaned.casefold()
    if any(word in lowered for word in organisational):
        return False
    return all(w[:1].isupper() and w[1:].islower() for w in words if w)


def _supported(record: object, pages: dict[str, str]) -> bool:
    """Whether an evidence-bearing record's quote is genuinely on its page."""
    url = getattr(record, "evidence_url", "")
    quote = getattr(record, "evidence_quote", "")
    page_text = pages.get(url)
    if page_text is None:
        return False
    if not quote_is_supported(quote, page_text):
        return False
    return not contains_personal_data(quote)


def coverage_only(mentions: tuple[MediaMention, ...], *, domain: str) -> tuple[MediaMention, ...]:
    """Keep mentions that are actually coverage, and drop the company's own blog.

    The first run of this recorded 75 mentions across the segment and every
    single one was a post on the company's own news page, with no outlet named.
    That is a blog, the site already carries a `blog` signal for it, and
    counting it as press turned the section into a second copy of their news
    listing — while the coverage component of the traction score sat at zero
    for everybody, measuring nothing.

    Two ways to survive: name an outlet, or live somewhere other than the
    company's own domain. A press page that says "featured in the NZZ" is a
    claim about real coverage even though the page is self-published; an
    untitled post on `blog.example.ch` is not.
    """
    root = domain.lower().removeprefix("www.")

    def is_own_site(url: str) -> bool:
        host = urlparse(url).hostname or ""
        host = host.lower().removeprefix("www.")
        # Subdomains count as the same company: blog.example.ch is theirs.
        return host == root or host.endswith(f".{root}")

    return tuple(m for m in mentions if (m.outlet and m.outlet.strip()) or not is_own_site(m.url))


def filter_evidence_lists(
    profile: CompanyProfile, pages: dict[str, str]
) -> tuple[CompanyProfile, dict[str, int]]:
    """Apply the evidence rule to case studies, clients and products.

    Same standard as offerings, for the same reason: a claim whose quote is not
    on the page it cites is a claim nobody made. Counted separately so a
    regression in one kind of extraction is visible rather than averaged away.
    """
    dropped: dict[str, int] = {}

    kept_cases = tuple(c for c in profile.case_studies if _supported(c, pages))
    dropped["case_studies"] = len(profile.case_studies) - len(kept_cases)

    kept_clients = tuple(
        c
        for c in profile.named_clients
        if _supported(c, pages) and not looks_like_a_person(c.client_name)
    )
    dropped["named_clients"] = len(profile.named_clients) - len(kept_clients)

    kept_products = tuple(p for p in profile.products if _supported(p, pages))
    dropped["products"] = len(profile.products) - len(kept_products)

    # A mention is judged on the quote on the company's *own* page, not on the
    # article it links to — that article has not been fetched and may well be
    # paywalled. What is being verified is that the company really does claim
    # this coverage, which is the only thing this stage is in a position to know.
    kept_mentions = coverage_only(
        tuple(m for m in profile.media_mentions if _supported(m, pages)),
        domain=profile.domain,
    )
    dropped["media_mentions"] = len(profile.media_mentions) - len(kept_mentions)

    updated = profile.model_copy(
        update={
            "case_studies": kept_cases,
            "named_clients": kept_clients,
            "products": kept_products,
            "media_mentions": kept_mentions,
        }
    )
    return updated, dropped


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


#: Attribute lists that were extracted and then thrown away for several weeks:
#: the model returned them, nothing stored them, and the interface showed a
#: company as having no technologies when the pipeline had read them off the
#: page. Mapped to the field name used in `company_field`.
ATTRIBUTE_FIELDS: Final[tuple[str, ...]] = (
    "industries_served",
    "workshop_formats",
    "technologies",
    "cloud_providers",
    "hosting",
    "certifications",
)

#: Which of those are literal strings copied off the page, and so can be
#: checked against it. The rest are classifications into a vocabulary — the
#: word "on_premises" is not expected to appear on a site that says "in Ihrem
#: eigenen Rechenzentrum" — and a substring check would silently delete them.
GROUNDABLE_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {"technologies", "cloud_providers", "certifications"}
)


def _mentioned(term: str, haystack: str) -> bool:
    """Whether a term genuinely appears in the page text.

    Word-boundary matched at the start, for the reason the tag grounding is:
    a bare substring check let `rag` match *leverage*, *storage* and *fragen*,
    and reported 148 companies doing retrieval augmentation when about 20 were.
    """
    needle = term.casefold().strip()
    if len(needle) < 2:
        return False
    return re.search(rf"\b{re.escape(needle)}", haystack) is not None


def _write_attributes(
    conn: sqlite3.Connection,
    company_id: int,
    profile: CompanyProfile,
    pages: dict[str, str],
    extractor: str,
) -> int:
    """Store the multi-valued attributes, grounded where they can be.

    These go in `company_field` rather than `tag` because `tag` belongs to
    `classify`, which enforces the segment's declared vocabulary. Writing tags
    from here once bypassed those checks and put 130 near-synonymous service
    types into the interface.
    """
    haystack = " ".join(pages.values()).casefold()
    primary = next(iter(pages), "")
    now = _now()

    # Placeholders only — the interpolated text is a run of "?" derived from a
    # module constant, never from anything a page or a model produced.
    placeholders = ",".join("?" * len(ATTRIBUTE_FIELDS))
    conn.execute(
        f"DELETE FROM company_field WHERE company_id = ? AND field IN ({placeholders})",  # noqa: S608
        (company_id, *ATTRIBUTE_FIELDS),
    )

    written = 0
    for field_name in ATTRIBUTE_FIELDS:
        values = getattr(profile, field_name, ()) or ()
        for raw in values:
            value = str(raw).strip()
            if not value:
                continue
            if field_name == "industries_served":
                canonical = canonical_industry(value)
                if canonical is None:
                    continue
                value = canonical
            elif field_name in GROUNDABLE_ATTRIBUTES and not _mentioned(value, haystack):
                continue

            conn.execute(
                """
                INSERT INTO company_field
                  (company_id, field, value, source_url, evidence_quote,
                   confidence, extractor, extracted_at)
                VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (company_id, field_name, value, primary, extractor, now),
            )
            written += 1
    return written


def _write_depth(
    conn: sqlite3.Connection,
    company_id: int,
    profile: CompanyProfile,
    report: ExtractReport,
) -> None:
    """Replace this company's case studies, clients, products and signals.

    Replaced rather than merged: a re-extraction reflects the site as it is now,
    and merging would accumulate claims a company has since removed.
    """
    now = _now()
    for table in ("case_study", "client_reference", "product", "media_mention", "site_signal"):
        conn.execute(f"DELETE FROM {table} WHERE company_id = ?", (company_id,))  # noqa: S608

    for case in profile.case_studies:
        conn.execute(
            """
            INSERT INTO case_study
              (company_id, title, industry, summary, evidence_url, evidence_quote, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                case.title,
                canonical_industry(case.industry),
                case.summary,
                case.evidence_url,
                case.evidence_quote,
                now,
            ),
        )
    report.case_studies += len(profile.case_studies)

    for mention in profile.media_mentions:
        conn.execute(
            """
            INSERT OR IGNORE INTO media_mention
              (company_id, headline, outlet, kind, published_year, url,
               is_self_published, evidence_quote, evidence_url,
               discovered_via, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'own_site', ?)
            """,
            (
                company_id,
                mention.headline,
                mention.outlet,
                mention.kind,
                mention.published_year,
                mention.url,
                int(mention.is_self_published),
                mention.evidence_quote,
                mention.evidence_url,
                now,
            ),
        )
    report.mentions += len(profile.media_mentions)

    for client in profile.named_clients:
        conn.execute(
            """
            INSERT INTO client_reference
              (company_id, client_name, industry, relationship,
               evidence_url, evidence_quote, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                client.client_name,
                canonical_industry(client.industry),
                client.relationship,
                client.evidence_url,
                client.evidence_quote,
                now,
            ),
        )
    report.clients += len(profile.named_clients)

    for item in profile.products:
        conn.execute(
            """
            INSERT INTO product
              (company_id, name, kind, summary, evidence_url, evidence_quote, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                item.name,
                item.kind,
                item.summary,
                item.evidence_url,
                item.evidence_quote,
                now,
            ),
        )
    report.products += len(profile.products)

    primary = next(iter(profile.model_dump().get("_pages", [])), None) or ""
    for signal, present in profile.site_signals.items():
        if signal not in SITE_SIGNALS:
            continue
        conn.execute(
            """
            INSERT INTO site_signal (company_id, signal, present, evidence_url, extracted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(company_id, signal) DO UPDATE SET
              present = excluded.present, extracted_at = excluded.extracted_at
            """,
            (company_id, signal, 1 if present else 0, primary or None, now),
        )
        report.signals += 1


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

        profile, depth_dropped = filter_evidence_lists(strip_personal_data(result.value), pages)
        report.depth_dropped += sum(depth_dropped.values())
        profile, dropped = drop_unsupported(profile, pages)
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

        _write_depth(conn, company_id, profile, report)
        report.attributes += _write_attributes(conn, company_id, profile, pages, extractor)

        db.upsert_company(
            conn,
            domain=domain,
            canonical_name=profile.legal_name or domain,
            one_liner=profile.one_liner or None,
            legal_name=profile.legal_name,
            headcount_est=profile.headcount_estimate,
            founded_year=profile.founded_year,
            street=profile.street,
            postal_code=profile.postal_code,
            city=swiss.canonical_city(profile.city),
            # Folded to a two-letter code, or dropped. A model answers this
            # field with whatever the site says — four languages, two
            # registers, and occasionally somewhere that is not Swiss at all.
            canton=swiss.canton_code(profile.canton),
            languages=",".join(swiss.canonical_languages(profile.languages)) or None,
            last_enriched=_now(),
        )

        for name, value in (
            ("street", profile.street),
            ("postal_code", profile.postal_code),
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

        # Facet tags are deliberately NOT written here. classify.py owns them,
        # because only it checks them against the segment's vocabulary and
        # against what the site actually said. Writing them from both places
        # meant every check classify applied was bypassed by this loop — which
        # is how "tax", "esg" and "gala dinner" reached a service_type column
        # whose declared vocabulary has seven entries.

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
