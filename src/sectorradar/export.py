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

FORMATS = ("csv", "xlsx", "geojson", "json")

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

    rows = rows_for(conn, segment, accepted_only=accepted_only)
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = export_dir / f"{segment.slug}-{stamp}.{fmt}"

    writers = {
        "csv": _write_csv,
        "json": _write_json,
        "xlsx": _write_xlsx,
        "geojson": _write_geojson,
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
    row = conn.execute(
        "SELECT payload FROM snapshot WHERE segment_slug = ? ORDER BY taken_at DESC LIMIT 1",
        (segment.slug,),
    ).fetchone()
    if row is None:
        return None
    parsed: list[dict[str, Any]] = json.loads(row["payload"])
    return parsed
