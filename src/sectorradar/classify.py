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

import sqlite3
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
    undecided: int = 0
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
        "\n".join(f"- `{facet}`: {', '.join(values)}" for facet, values in segment.facets.items())
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


def _to_classify(conn: sqlite3.Connection, segment: Segment, *, force: bool) -> list[sqlite3.Row]:
    """Companies eligible for a tiering decision.

    Reviewed rows are excluded unless forced. A human who has accepted or
    re-tiered a company has made the authoritative decision, and quietly
    overwriting it is how an hour of review evaporates.
    """
    clause = "" if force else "AND COALESCE(m.review_state, 'pending') = 'pending'"
    sql = f"""
        SELECT c.id, c.domain, c.one_liner, c.city, c.canton, c.country, m.review_state
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
            report.undecided += 1
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

        for facet, values in decision.facets.items():
            if segment.facets and facet not in segment.facets:
                # Facets are fixed; values are open. A new facet is a config
                # change, not something a model gets to invent mid-run.
                log.warning("classify.unknown_facet", facet=facet, domain=domain)
                continue
            for value in values:
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
        undecided=report.undecided,
        by_tier=report.by_tier,
        cost_usd=round(report.usage.cost_usd, 4),
    )
    return report
