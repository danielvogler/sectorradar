"""Tiering: where a company sits relative to one segment's inclusion rule.

A separate call from extraction, for two reasons. Tiering depends on the
segment definition while extraction does not, so the same extracted profile
serves any number of segments. And the inclusion rule is the thing that gets
iterated on — being able to re-tier without re-crawling and re-extracting is
what makes that iteration cheap.

The segment's ``inclusion`` prose and ``tiers`` map go into the prompt verbatim.
Paraphrasing them here would mean the boundary lives in two places and drifts.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sectorradar import db, swiss
from sectorradar.config import Segment, Settings
from sectorradar.llm import LLMClient, Usage
from sectorradar.logging import get_logger
from sectorradar.models import Classification

log = get_logger(__name__)

PROMPT_VERSION = "classify.v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


@dataclass
class ClassifyReport:
    considered: int = 0
    classified: int = 0
    #: Candidates the classifier judged *not* to belong in the segment. This
    #: was called `undecided`, which reads as a backlog to work through rather
    #: than a decision already taken — and the interface duly showed all 109 of
    #: them as companies, reporting their missing addresses as a data gap.
    excluded: int = 0
    out_of_area: int = 0
    tags_ungrounded: int = 0
    tags_out_of_vocabulary: int = 0
    failed: int = 0
    skipped_reviewed: int = 0
    by_tier: dict[int, int] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)


def _format_offerings(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "(none recorded — the site did not clearly claim any service)"
    return "\n".join(
        f'- **{r["label"]}** — "{r["evidence_quote"]}" ({r["evidence_url"]})' for r in rows
    )


def _format_facts(rows: list[sqlite3.Row]) -> str:
    visible = [r for r in rows if not str(r["field"]).startswith("_")]
    if not visible:
        return "(none recorded)"
    return "\n".join(f"- {r['field']}: {r['value']}" for r in visible)


def _location(city: str | None, canton: str | None, country: str | None) -> str:
    """What the extraction step found about where this company sits.

    Rendered explicitly rather than left out when empty, because "the site was
    read and no address was found" is evidence the classifier should weigh, and
    an absent field reads as an oversight instead.
    """
    parts = [p for p in (city, swiss.canton_name(canton) or canton, country) if p]
    return ", ".join(parts) if parts else "none recorded"


def build_prompt(
    segment: Segment,
    domain: str,
    one_liner: str | None,
    offerings: list[sqlite3.Row],
    facts: list[sqlite3.Row],
    *,
    city: str | None = None,
    canton: str | None = None,
    country: str | None = None,
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    tiers = "\n".join(f"- **Tier {n}** — {text}" for n, text in sorted(segment.tiers.items()))
    facets = (
        "\n".join(
            f"- `{facet}`: {', '.join(segment.facet_values(facet))}" for facet in segment.facets
        )
        or "- (no controlled facets for this segment)"
    )
    return template.format(
        country=segment.geo.country,
        location=_location(city, canton, country),
        inclusion=segment.inclusion.strip(),
        tiers=tiers,
        facets=facets,
        domain=domain,
        one_liner=one_liner or "(none recorded)",
        offerings=_format_offerings(offerings),
        facts=_format_facts(facts),
    )


#: Words that count as support for a controlled facet value. A tag claims the
#: company *does* something, so it should be traceable to something the site
#: said — the same standard offerings are held to.
#:
#: Without this, tags were pure assertion: 148 companies came back tagged `rag`
#: and 110 of them had no offering mentioning retrieval or RAG at all, and
#: `strategy` and `automation` were applied to almost every company in the
#: segment, which makes the facet useless as a filter.
TAG_EVIDENCE: dict[str, tuple[str, ...]] = {
    # Building
    "agent_dev": ("agent", "agentic", "autonom", "copilot", "assistant"),
    "chatbot_dev": ("chatbot", "chat bot", "conversational", "dialog", "voicebot", "sprachassist"),
    "rag": ("rag", "retrieval", "vector", "embedding", "knowledge base", "wissensdatenbank"),
    "automation": ("automat", "workflow", "prozess", "process", "rpa", "n8n", "zapier"),
    "custom_software": ("softwareentwicklung", "software development", "individualsoftware"),
    "integration": ("integration", "integrier", "schnittstelle", "api", "erp", "anbindung"),
    "data_engineering": ("data engineering", "datenpipeline", "data pipeline", "etl", "warehouse"),
    "ml_dev": ("machine learning", "maschinelles lernen", "predict", "forecast", "modell"),
    "mlops": ("mlops", "ml ops", "deployment", "monitoring", "pipeline", "productioniz"),
    "infrastructure": ("infrastruktur", "infrastructure", "cloud", "kubernetes", "hosting"),
    # Deciding
    "strategy": ("strateg", "roadmap", "beratung", "consult", "conseil", "advisory"),
    "use_case_discovery": ("use case", "anwendungsfall", "ideation", "discovery", "potenzial"),
    "assessment": ("assessment", "audit", "readiness", "analyse", "standortbestimmung", "check"),
    "prototyping": ("prototyp", "proof of concept", "poc", "pilot", "mvp", "machbarkeit"),
    # Teaching
    "workshops": ("workshop", "schulung", "seminar", "kurs", "formation", "webinar"),
    "training": ("training", "schulung", "kurs", "formation", "academy", "akademie", "lernen"),
    "executive_briefing": ("executive", "geschäftsleitung", "keynote", "vortrag", "verwaltungsrat"),
    # Ongoing
    "managed_service": ("managed service", "betrieb", "operations", "wartung", "betreuung"),
    "support": ("support", "helpdesk", "wartung", "maintenance", "sla"),
    "staffing": ("staffing", "staff aug", "recruit", "personal", "interim", "outsourc"),
    "staff_aug": ("staff aug", "staffing", "augmentation", "interim", "outsourc"),
    # Edges
    "governance": ("governance", "ai act", "compliance", "risiko", "risk", "richtlinie", "ethik"),
    "data_protection": ("datenschutz", "data protection", "dsgvo", "gdpr", "privacy", "revdsg"),
    "ux_design": ("ux", "user experience", "design", "interface", "usability"),
    "product": ("produkt", "product", "plattform", "platform", "saas", "lizenz", "licence"),
}


def tag_is_grounded(value: str, evidence: str, keywords: Sequence[str] = ()) -> bool:
    """Whether the company's own text supports a facet value.

    Matches on word boundaries, not substrings. A plain ``in`` test looked
    right and was badly wrong: the three-letter token ``rag`` is inside
    ``leverage``, ``storage``, ``average``, ``drag`` and the German ``fragen``,
    and "leverage" appears in roughly every second piece of marketing copy —
    which is how 95 companies came back doing retrieval-augmented generation.

    Three sources of evidence words, in order:

    1. ``keywords`` from the segment file, which is where market-specific
       vocabulary belongs. A cybersecurity segment needs *Penetrationstest*
       and *Schwachstellenanalyse*; nothing in this module could know that.
    2. :data:`TAG_EVIDENCE`, which covers the values common enough to be worth
       shipping and is where the agentic-AI vocabulary lives.
    3. The value itself, spaced out. A reasonable last resort in English and a
       poor one otherwise: ``pentest`` never appears on a page that offers
       *Penetrationstests*, so the tag is dropped and the market looks emptier
       than it is.
    """
    haystack = evidence.casefold()
    needles = tuple(keywords) or TAG_EVIDENCE.get(value, (value.replace("_", " "),))
    return any(
        re.search(rf"\b{re.escape(needle.casefold())}", haystack) is not None for needle in needles
    )


def fails_geography(city: str | None, geocode_status: str | None) -> bool:
    """Whether recorded location contradicts the segment's country.

    A deterministic gate, deliberately not left to the prompt. The classifier is
    *told* to check geography first and still returned tier 1 for companies
    whose own recorded city was Toronto, Calgary, Kochi, Austin and Dortmund —
    it had the evidence in front of it and tiered them anyway. Persuasion is
    the wrong instrument for a rule that can be evaluated.

    The evidence is the geocoder's own verdict, which only accepts places
    inside the country and only accepts an answer that is actually the place
    asked for. ``geocode_status`` records what it concluded.

    It has to be the *status*, not a null ``lat``. A missing coordinate means
    two different things — "looked for and not found" and "never looked for" —
    and reading it as the first excluded 21 real companies in Zürich, Geneva
    and Lugano whose tier had simply kept them out of the geocoding pass.
    """
    return bool(city) and geocode_status == "not_found"


def _to_classify(conn: sqlite3.Connection, segment: Segment, *, force: bool) -> list[sqlite3.Row]:
    """Companies eligible for a tiering decision.

    Reviewed rows are excluded unless forced. A human who has accepted or
    re-tiered a company has made the authoritative decision, and quietly
    overwriting it is how an hour of review evaporates.
    """
    clause = "" if force else "AND COALESCE(m.review_state, 'pending') = 'pending'"
    sql = f"""
        SELECT c.id, c.domain, c.one_liner, c.city, c.canton, c.country,
               c.geocode_status, m.review_state
          FROM company c
          JOIN membership m ON m.company_id = c.id
         WHERE m.segment_slug = ?
           {clause}
      ORDER BY c.id
    """  # noqa: S608 - `clause` is one of two literals chosen above
    return conn.execute(sql, (segment.slug,)).fetchall()


def classify(
    conn: sqlite3.Connection,
    segment: Segment,
    settings: Settings,
    client: LLMClient,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> ClassifyReport:
    """Assign a tier, a written rationale and facet tags to each company."""
    report = ClassifyReport()
    source = f"{PROMPT_VERSION}/{client.model}"

    reviewed = conn.execute(
        """
        SELECT COUNT(*) AS n FROM membership
         WHERE segment_slug = ? AND COALESCE(review_state, 'pending') != 'pending'
        """,
        (segment.slug,),
    ).fetchone()
    if not force:
        report.skipped_reviewed = int(reviewed["n"])

    for row in _to_classify(conn, segment, force=force):
        company_id, domain = int(row["id"]), str(row["domain"])
        report.considered += 1

        if fails_geography(row["city"], row["geocode_status"]):
            # Settled without spending a call: the company's own recorded city
            # could not be found in the segment's country.
            report.out_of_area += 1
            if not dry_run:
                db.upsert_membership(
                    conn,
                    segment_slug=segment.slug,
                    company_id=company_id,
                    tier=None,
                    tier_rationale=(
                        f"Excluded on geography: recorded location '{row['city']}' is not in "
                        f"{segment.geo.country}. The segment's inclusion rule requires a "
                        "presence there."
                    ),
                    relevance=0.0,
                )
                conn.commit()
            log.info("classify.out_of_area", domain=domain, city=row["city"])
            continue

        offerings = conn.execute(
            "SELECT label, evidence_url, evidence_quote FROM offering WHERE company_id = ?",
            (company_id,),
        ).fetchall()
        facts = conn.execute(
            "SELECT field, value FROM company_field WHERE company_id = ?", (company_id,)
        ).fetchall()

        prompt = build_prompt(
            segment,
            domain,
            row["one_liner"],
            offerings,
            facts,
            city=row["city"],
            canton=row["canton"],
            country=row["country"],
        )
        result = client.structured(prompt, Classification)
        report.usage = report.usage + result.usage

        if result.value is None:
            report.failed += 1
            log.warning("classify.no_result", domain=domain)
            continue

        decision = result.value
        if decision.tier is None:
            report.excluded += 1
        else:
            report.classified += 1
            report.by_tier[decision.tier] = report.by_tier.get(decision.tier, 0) + 1

        if dry_run:
            continue

        db.upsert_membership(
            conn,
            segment_slug=segment.slug,
            company_id=company_id,
            tier=decision.tier,
            tier_rationale=decision.tier_rationale,
            relevance=decision.relevance,
        )

        # What the site actually said, for checking tags against.
        evidence_text = " ".join(
            [str(row["one_liner"] or "")]
            + [f"{o['label']} {o['evidence_quote']}" for o in offerings]
        )

        for facet, values in decision.facets.items():
            if segment.facets and facet not in segment.facets:
                # Facets are fixed. A new facet is a config change, not
                # something a model gets to invent mid-run.
                log.warning("classify.unknown_facet", facet=facet, domain=domain)
                continue

            allowed = set(segment.facet_values(facet))
            for value in values:
                # Two checks, both of which the previous version skipped.
                #
                # The vocabulary is the segment's. Letting the model coin values
                # freely produced 130+ distinct service types including "gala
                # dinner", "podcast" and "magazine" — unusable as a filter.
                # Rejected values are logged so frequent ones can be promoted
                # into the YAML deliberately.
                if allowed and value not in allowed:
                    report.tags_out_of_vocabulary += 1
                    log.debug("classify.tag_out_of_vocabulary", facet=facet, value=value)
                    continue
                # And the tag has to be traceable to something the site said.
                if not tag_is_grounded(value, evidence_text, segment.facet_keywords(facet, value)):
                    report.tags_ungrounded += 1
                    log.debug("classify.tag_ungrounded", facet=facet, value=value, domain=domain)
                    continue
                conn.execute(
                    """
                    INSERT INTO tag (company_id, facet, value, confidence, source_url)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, facet, value) DO UPDATE SET
                      confidence = excluded.confidence
                    """,
                    (company_id, facet, value, decision.relevance, source),
                )

        # Same reasoning as extract: one paid call per company, so commit as
        # each lands rather than risking the lot on a clean exit.
        conn.commit()

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    log.info(
        "classify.done",
        segment=segment.slug,
        considered=report.considered,
        classified=report.classified,
        excluded=report.excluded,
        out_of_area=report.out_of_area,
        tags_ungrounded=report.tags_ungrounded,
        tags_out_of_vocabulary=report.tags_out_of_vocabulary,
        by_tier=report.by_tier,
        cost_usd=round(report.usage.cost_usd, 4),
    )
    return report
