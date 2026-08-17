"""Addresses to coordinates, cache-first.

At the scale this project targets — a couple of hundred kept companies — the
geocoding budget is effectively free, so the engineering here is about not
being rude rather than not being expensive: cache every answer to disk keyed on
the normalised query, and never ask the same question twice.

swisstopo is the primary because the segment is Swiss and its data is better
here than any global service. Nominatim is the fallback, at its published one
request per second with a real User-Agent.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from sectorradar import swiss
from sectorradar.config import Segment, Settings
from sectorradar.logging import get_logger
from sectorradar.models import GeoPoint

log = get_logger(__name__)

SWISSTOPO_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: Nominatim's usage policy. Not negotiable, and not worth arguing with.
NOMINATIM_MIN_INTERVAL = 1.0

# Swiss cantons, for turning a city into a more specific query.
CANTON_NAMES: dict[str, str] = {
    "ZH": "Zürich",
    "BE": "Bern",
    "LU": "Luzern",
    "UR": "Uri",
    "SZ": "Schwyz",
    "OW": "Obwalden",
    "NW": "Nidwalden",
    "GL": "Glarus",
    "ZG": "Zug",
    "FR": "Fribourg",
    "SO": "Solothurn",
    "BS": "Basel-Stadt",
    "BL": "Basel-Landschaft",
    "SH": "Schaffhausen",
    "AR": "Appenzell Ausserrhoden",
    "AI": "Appenzell Innerrhoden",
    "SG": "St. Gallen",
    "GR": "Graubünden",
    "AG": "Aargau",
    "TG": "Thurgau",
    "TI": "Ticino",
    "VD": "Vaud",
    "VS": "Valais",
    "NE": "Neuchâtel",
    "GE": "Genève",
    "JU": "Jura",
}


@dataclass
class GeocodeReport:
    considered: int = 0
    from_cache: int = 0
    geocoded: int = 0
    failed: int = 0
    skipped_no_address: int = 0


class GeocodeCache:
    """A JSON file mapping a normalised query to a result, or to a miss.

    Misses are cached too. A place that could not be found this morning will
    not be found this afternoon, and re-asking is exactly the kind of pointless
    traffic the cache exists to prevent.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any] | None] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("geocode.cache_unreadable", path=str(path), error=str(exc))
                self._data = {}

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str) -> GeoPoint | None:
        raw = self._data.get(key)
        return GeoPoint.model_validate(raw) if raw else None

    def put(self, key: str, point: GeoPoint | None) -> None:
        self._data[key] = point.model_dump() if point else None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )


def build_query(
    street: str | None, postal_code: str | None, city: str | None, canton: str | None
) -> str | None:
    """The most specific query the known fields support, or None if too little."""
    parts = [p.strip() for p in (street, postal_code, city) if p and p.strip()]
    if not parts:
        name = swiss.canton_name(swiss.canton_code(canton))
        if name:
            parts = [name]
    if not parts:
        return None
    return ", ".join([*parts, "Switzerland"])


def cache_key(query: str) -> str:
    return " ".join(query.lower().split())


def _swisstopo(client: httpx.Client, query: str) -> GeoPoint | None:
    response = client.get(
        SWISSTOPO_URL,
        params={"type": "locations", "searchText": query, "limit": 1, "sr": "4326"},
        timeout=15.0,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return None

    attrs = results[0].get("attrs") or {}
    # swisstopo returns WGS84 as lat/lon when sr=4326 is requested.
    lat, lon = attrs.get("lat"), attrs.get("lon")
    if lat is None or lon is None:
        return None
    return GeoPoint(
        lat=float(lat),
        lon=float(lon),
        provider="swisstopo",
        matched_address=attrs.get("label"),
    )


def _nominatim(client: httpx.Client, query: str, user_agent: str) -> GeoPoint | None:
    response = client.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "ch"},
        headers={"User-Agent": user_agent},
        timeout=15.0,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    first = results[0]
    return GeoPoint(
        lat=float(first["lat"]),
        lon=float(first["lon"]),
        provider="nominatim",
        matched_address=first.get("display_name"),
    )


def geocode_query(
    client: httpx.Client, query: str, user_agent: str, *, last_nominatim: list[float]
) -> GeoPoint | None:
    """Try swisstopo, then Nominatim. Returns None when neither knows the place."""
    try:
        point = _swisstopo(client, query)
        if point is not None:
            return point
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("geocode.swisstopo_failed", query=query, error=str(exc))

    elapsed = time.monotonic() - last_nominatim[0]
    if elapsed < NOMINATIM_MIN_INTERVAL:
        time.sleep(NOMINATIM_MIN_INTERVAL - elapsed)
    last_nominatim[0] = time.monotonic()

    try:
        return _nominatim(client, query, user_agent)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("geocode.nominatim_failed", query=query, error=str(exc))
        return None


def _eligible_rows(conn: sqlite3.Connection, segment: Segment) -> list[sqlite3.Row]:
    """Companies worth spending a request on.

    The specification says to geocode only accepted companies or those at tier
    1-2, to avoid burning requests on the tier-3 pool. Unclassified companies
    are included as well, because the tier-3 pool does not exist until
    ``classify`` has run — excluding NULL would mean nothing is ever mappable
    before classification, which puts the map behind two more stages for no
    benefit.
    """
    return conn.execute(
        """
        SELECT c.id, c.domain, c.street, c.postal_code, c.city, c.canton
          FROM company c
          JOIN membership m ON m.company_id = c.id
         WHERE m.segment_slug = ?
           AND c.lat IS NULL
           AND (m.review_state = 'accepted' OR m.tier IS NULL OR m.tier <= 2)
      ORDER BY c.id
        """,
        (segment.slug,),
    ).fetchall()


def geocode(
    conn: sqlite3.Connection,
    segment: Segment,
    settings: Settings,
    *,
    dry_run: bool = False,
) -> GeocodeReport:
    """Fill in coordinates for every eligible company that lacks them."""
    cache = GeocodeCache(settings.cache_dir / "geocode.json")
    report = GeocodeReport()
    user_agent = settings.user_agent()
    last_nominatim = [0.0]

    rows = _eligible_rows(conn, segment)
    with httpx.Client(follow_redirects=True, headers={"User-Agent": user_agent}) as client:
        for row in rows:
            report.considered += 1
            query = build_query(row["street"], row["postal_code"], row["city"], row["canton"])
            if query is None:
                report.skipped_no_address += 1
                continue

            key = cache_key(query)
            if key in cache:
                point = cache.get(key)
                report.from_cache += 1
            else:
                point = geocode_query(client, query, user_agent, last_nominatim=last_nominatim)
                cache.put(key, point)

            if point is None:
                report.failed += 1
                continue

            report.geocoded += 1
            if not dry_run:
                conn.execute(
                    "UPDATE company SET lat = ?, lon = ? WHERE id = ?",
                    (point.lat, point.lon, row["id"]),
                )

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
        cache.save()

    log.info(
        "geocode.done",
        segment=segment.slug,
        considered=report.considered,
        geocoded=report.geocoded,
        cached=report.from_cache,
        failed=report.failed,
        no_address=report.skipped_no_address,
    )
    return report
