"""Export and snapshots.

Two related jobs. An **export** is a file for somebody else to open — CSV for a
spreadsheet, XLSX for a colleague who wants one, GeoJSON for anything that draws
maps. A **snapshot** is for the tool itself: a frozen copy of the accepted set,
so that in three months "who is new, who repositioned" is answerable. Neither
question can be reconstructed from a mutable table after the fact, which is why
the snapshot is taken rather than derived.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sectorradar.config import Segment
from sectorradar.logging import get_logger

log = get_logger(__name__)

FORMATS = ("csv", "xlsx", "geojson", "json", "web")

COLUMNS = (
    "domain",
    "canonical_name",
    "legal_name",
    "tier",
    "tier_rationale",
    "relevance",
    "review_state",
    "one_liner",
    "city",
    "canton",
    "country",
    "lat",
    "lon",
    "headcount_est",
    "founded_year",
    "first_seen",
    "last_enriched",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def rows_for(
    conn: sqlite3.Connection, segment: Segment, *, accepted_only: bool = False
) -> list[dict[str, Any]]:
    """The exportable view of a segment."""
    clause = "AND m.review_state = 'accepted'" if accepted_only else ""
    sql = f"""
        SELECT c.domain, c.canonical_name, c.legal_name, m.tier, m.tier_rationale,
               m.relevance, COALESCE(m.review_state, 'pending') AS review_state,
               c.one_liner, c.city, c.canton, c.country, c.lat, c.lon,
               c.headcount_est, c.founded_year, c.first_seen, c.last_enriched
          FROM company c
          JOIN membership m ON m.company_id = c.id
         WHERE m.segment_slug = ?
           {clause}
      ORDER BY m.tier IS NULL, m.tier, c.canonical_name
    """  # noqa: S608 - `clause` is one of two literals chosen above
    return [dict(row) for row in conn.execute(sql, (segment.slug,)).fetchall()]


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(rows: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_xlsx(rows: list[dict[str, Any]], path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:  # pragma: no cover - openpyxl always provides one
        sheet = workbook.create_sheet()
    sheet.title = "companies"
    sheet.append(list(COLUMNS))
    for row in rows:
        sheet.append([row.get(column) for column in COLUMNS])
    workbook.save(path)


def _write_geojson(rows: list[dict[str, Any]], path: Path) -> None:
    """GeoJSON of everything that has coordinates.

    Rows without them are omitted rather than placed at (0, 0), which would put
    a Swiss consultancy in the Gulf of Guinea.
    """
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {k: v for k, v in row.items() if k not in ("lat", "lon")},
        }
        for row in rows
        if row.get("lat") is not None and row.get("lon") is not None
    ]
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _segment_document(segment: Segment) -> dict[str, Any]:
    """The question this dataset answers, carried alongside the answer.

    Numbers about a market are uninterpretable without the definition of the
    market. Somebody handed the built page needs to see the inclusion rule, the
    tier meanings and the queries that went looking, or they cannot tell the
    difference between "there are no AI firms in Uri" and "nothing we ran would
    have found one".

    `own_domains` is deliberately not here. It lives in a gitignored local
    overlay, and a published page is exactly the wrong place for it.
    """
    websearch = segment.source("websearch")
    raw_queries = getattr(websearch, "queries", None) or []
    queries: list[str] = [str(q) for q in raw_queries]

    return {
        "slug": segment.slug,
        "name": segment.name,
        "country": segment.geo.country,
        "inclusion": segment.inclusion,
        "tiers": {str(k): v for k, v in segment.tiers.items()},
        "enrich_tiers": list(segment.enrich_tiers),
        "facets": {k: list(v) for k, v in segment.facets.items()},
        "sources_enabled": segment.enabled_sources(),
        "queries": queries,
        "gold_set_size": len(segment.gold_set),
    }


def web_document(conn: sqlite3.Connection, segment: Segment) -> dict[str, Any]:
    """Everything a front end needs, in one JSON document.

    This is the seam. A browser reads it with no server and no Python, which is
    what makes the built site a folder you can hand to somebody. A later step
    that publishes to cloud storage writes this same document, so neither the
    front end nor the pipeline needs to know the other's shape.

    Aggregates are precomputed rather than left to the client: a view that
    derives its own totals is a view that can disagree with `sectorradar stats`
    about what is true.
    """
    from sectorradar import analytics, traction

    companies = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.id, c.domain, c.canonical_name, c.legal_name, c.one_liner,
                   c.street, c.postal_code, c.city, c.canton, c.country,
                   c.lat, c.lon, c.geocode_status, c.headcount_est, c.founded_year, c.languages,
                   m.tier, m.tier_rationale, m.relevance,
                   COALESCE(m.review_state, 'pending') AS review_state
              FROM company c JOIN membership m ON m.company_id = c.id
             WHERE m.segment_slug = ?
          ORDER BY m.tier IS NULL, m.tier, c.canonical_name
            """,
            (segment.slug,),
        ).fetchall()
    ]

    owned = segment.owned()
    by_id = {int(c["id"]): c for c in companies}
    for company in companies:
        company["is_own"] = company["domain"] in owned
        company["size_band"] = analytics.band_for(company["headcount_est"])
        for key in (
            "offerings",
            "case_studies",
            "clients",
            "products",
            "mentions",
            "tags",
            "signals",
        ):
            company[key] = []

    def attach(sql: str, key: str) -> None:
        for row in conn.execute(sql, (segment.slug,)).fetchall():
            target = by_id.get(int(row["company_id"]))
            if target is not None:
                target[key].append({k: row[k] for k in row.keys() if k != "company_id"})  # noqa: SIM118

    attach(
        """SELECT o.company_id, o.label, o.evidence_url, o.evidence_quote
             FROM offering o JOIN membership m ON m.company_id = o.company_id
            WHERE m.segment_slug = ? ORDER BY o.id""",
        "offerings",
    )
    attach(
        """SELECT s.company_id, s.title, s.industry, s.summary, s.evidence_url, s.evidence_quote
             FROM case_study s JOIN membership m ON m.company_id = s.company_id
            WHERE m.segment_slug = ? ORDER BY s.id""",
        "case_studies",
    )
    attach(
        """SELECT r.company_id, r.client_name, r.industry, r.relationship,
                  r.evidence_url, r.evidence_quote
             FROM client_reference r JOIN membership m ON m.company_id = r.company_id
            WHERE m.segment_slug = ? ORDER BY r.id""",
        "clients",
    )
    attach(
        """SELECT p.company_id, p.name, p.kind, p.summary, p.evidence_url, p.evidence_quote
             FROM product p JOIN membership m ON m.company_id = p.company_id
            WHERE m.segment_slug = ? ORDER BY p.id""",
        "products",
    )
    attach(
        """SELECT n.company_id, n.headline, n.outlet, n.kind, n.published_year,
                  n.url, n.is_self_published, n.evidence_url, n.evidence_quote
             FROM media_mention n JOIN membership m ON m.company_id = n.company_id
            WHERE m.segment_slug = ? ORDER BY n.published_year DESC, n.id""",
        "mentions",
    )
    # Multi-valued attributes: technologies, cloud providers, hosting model,
    # certifications, training formats, industries served. Grouped per company
    # so the front end gets lists rather than having to pivot rows.
    for company in companies:
        company["attributes"] = {}
    for row in conn.execute(
        """SELECT f.company_id, f.field, f.value
             FROM company_field f JOIN membership m ON m.company_id = f.company_id
            WHERE m.segment_slug = ? AND f.field IN
                  ('industries_served','workshop_formats','technologies',
                   'cloud_providers','hosting','certifications')
         ORDER BY f.field, f.value""",
        (segment.slug,),
    ).fetchall():
        target = by_id.get(int(row["company_id"]))
        if target is not None:
            target["attributes"].setdefault(str(row["field"]), []).append(str(row["value"]))

    seo_rows = {
        int(r["company_id"]): r
        for r in conn.execute(
            """SELECT s.* FROM seo_profile s JOIN membership m ON m.company_id = s.company_id
                WHERE m.segment_slug = ?""",
            (segment.slug,),
        ).fetchall()
    }
    for company in companies:
        row = seo_rows.get(int(company["id"]))
        company["seo"] = (
            {
                "score": row["score"],
                "pages_analysed": row["pages_analysed"],
                "is_unknown": False,
                "title_length": row["title_length"],
                "description_length": row["description_length"],
                "languages_declared": row["languages_declared"],
                "median_word_count": row["median_word_count"],
                "has_canonical": bool(row["has_canonical"]),
                "has_hreflang": bool(row["has_hreflang"]),
                "has_open_graph": bool(row["has_open_graph"]),
                "has_viewport": bool(row["has_viewport"]),
                "blocks_indexing": bool(row["blocks_indexing"]),
                "image_alt_ratio": row["image_alt_ratio"],
                "schema_types": json.loads(row["schema_types"] or "[]"),
                "findings": json.loads(row["findings"] or "[]"),
                "components": json.loads(row["components"] or "{}"),
            }
            if row is not None
            else {
                "score": 0,
                "pages_analysed": 0,
                "is_unknown": True,
                "findings": [],
                "schema_types": [],
                "components": {},
            }
        )

    attach(
        """SELECT t.company_id, t.facet, t.value
             FROM tag t JOIN membership m ON m.company_id = t.company_id
            WHERE m.segment_slug = ? ORDER BY t.facet, t.value""",
        "tags",
    )
    attach(
        """SELECT g.company_id, g.signal, g.present
             FROM site_signal g JOIN membership m ON m.company_id = g.company_id
            WHERE m.segment_slug = ? ORDER BY g.signal""",
        "signals",
    )

    this_year = datetime.now(UTC).year
    for company in companies:
        independent = sum(1 for m in company["mentions"] if not m["is_self_published"])
        company["traction"] = traction.score(
            traction.Inputs(
                case_studies=len(company["case_studies"]),
                named_clients=len(company["clients"]),
                products=len(company["products"]),
                independent_mentions=independent,
                self_published_mentions=len(company["mentions"]) - independent,
                headcount=company["headcount_est"],
                founded_year=company["founded_year"],
                is_hiring=any(
                    s["signal"] == "careers_page" and s["present"] for s in company["signals"]
                ),
            ),
            this_year=this_year,
        ).to_dict()

    # Two different events, and conflating them under one date was misleading:
    # the page can be rebuilt at any time from data collected weeks earlier,
    # and "built today" then reads as "current as of today".
    collected = conn.execute(
        """SELECT MAX(c.last_enriched) AS at FROM company c
             JOIN membership m ON m.company_id = c.id
            WHERE m.segment_slug = ?""",
        (segment.slug,),
    ).fetchone()["at"]

    return {
        "generated_at": _now(),
        "collected_at": collected,
        "segment": _segment_document(segment),
        "companies": companies,
        "analytics": analytics.collect(conn, segment).to_dict(),
    }


def export(
    conn: sqlite3.Connection,
    segment: Segment,
    export_dir: Path,
    *,
    fmt: str = "csv",
    accepted_only: bool = False,
) -> Path:
    """Write the segment to a file and return its path."""
    if fmt not in FORMATS:
        msg = f"unknown export format '{fmt}'. Supported: {', '.join(FORMATS)}"
        raise ValueError(msg)

    export_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "web":
        # Stable filename, not dated: the front end imports a fixed path, and a
        # new date every run would mean editing the front end to see new data.
        path = export_dir / f"{segment.slug}.web.json"
        path.write_text(
            json.dumps(web_document(conn, segment), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        log.info("export.done", segment=segment.slug, fmt=fmt, path=str(path))
        return path

    rows = rows_for(conn, segment, accepted_only=accepted_only)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = export_dir / f"{segment.slug}-{stamp}.{fmt}"

    writers = {
        "csv": _write_csv,
        "json": _write_json,
        "xlsx": _write_xlsx,
        "geojson": _write_geojson,
        # No "web" entry: that format returns above, because it writes one
        # document about the whole segment rather than a row per company. A
        # placeholder here that raised NotImplementedError was unreachable and
        # read like an unfinished feature.
    }
    writers[fmt](rows, path)

    log.info("export.done", segment=segment.slug, fmt=fmt, rows=len(rows), path=str(path))
    return path


def snapshot(conn: sqlite3.Connection, segment: Segment, *, dry_run: bool = False) -> int:
    """Freeze the current accepted set. Returns the number of rows captured.

    Everything reviewed is captured, not only the accepted rows: knowing that a
    company was looked at and rejected is as useful three months later as
    knowing it was kept, and cheaper to store than to reconstruct.
    """
    rows = [
        row
        for row in rows_for(conn, segment)
        if row["review_state"] in ("accepted", "rejected", "needs_info") or row["tier"] is not None
    ]
    payload = json.dumps(rows, ensure_ascii=False)

    if not dry_run:
        conn.execute(
            "INSERT INTO snapshot (segment_slug, taken_at, payload) VALUES (?, ?, ?)",
            (segment.slug, _now(), payload),
        )
        conn.commit()

    log.info("snapshot.taken", segment=segment.slug, rows=len(rows))
    return len(rows)


def latest_snapshot(conn: sqlite3.Connection, segment: Segment) -> list[dict[str, Any]] | None:
    # `id` breaks the tie, because taken_at has second precision and two
    # snapshots within the same second are entirely ordinary — a fast run, or a
    # test. Ordering on the timestamp alone silently returns whichever row
    # SQLite happens to reach first.
    row = conn.execute(
        """
        SELECT payload FROM snapshot
         WHERE segment_slug = ?
      ORDER BY taken_at DESC, id DESC
         LIMIT 1
        """,
        (segment.slug,),
    ).fetchone()
    if row is None:
        return None
    parsed: list[dict[str, Any]] = json.loads(row["payload"])
    return parsed
