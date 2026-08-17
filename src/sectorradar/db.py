"""SQLite schema, migrations and the small set of write helpers.

``data/radar.db`` is the only interface between the pipeline and the Streamlit
app, so the schema is the real API of this project.

Two design rules drive it:

1. **Provenance per claim.** ``company_field`` is one row per extracted value
   with a source URL, not a wide table. When the interface says a firm runs
   GenAI workshops, you can click through to the sentence that says so.
2. **Snapshots.** "Who is new, who repositioned" cannot be reconstructed from a
   mutable table, so the accepted set is frozen on demand.

Migrations are an ordered list of DDL steps plus a ``schema_version`` table —
not Alembic. One file, one dependency-free upgrade path.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import structlog

log = structlog.get_logger(__name__)

# --------------------------------------------------------------------------
# Migrations. Append only: never edit a step that has shipped, add a new one.
# --------------------------------------------------------------------------

_MIGRATION_001_CORE: Final = """
CREATE TABLE IF NOT EXISTS segment (
  slug            TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  config_yaml     TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company (
  id              INTEGER PRIMARY KEY,
  uid             TEXT UNIQUE,
  domain          TEXT UNIQUE NOT NULL,
  canonical_name  TEXT NOT NULL,
  legal_name      TEXT,
  legal_form      TEXT,
  one_liner       TEXT,
  street          TEXT,
  postal_code     TEXT,
  city            TEXT,
  canton          TEXT,
  country         TEXT DEFAULT 'CH',
  lat             REAL,
  lon             REAL,
  headcount_est   INTEGER,
  founded_year    INTEGER,
  languages       TEXT,
  status          TEXT,
  first_seen      TEXT NOT NULL,
  last_enriched   TEXT
);

CREATE TABLE IF NOT EXISTS membership (
  segment_slug    TEXT NOT NULL REFERENCES segment(slug),
  company_id      INTEGER NOT NULL REFERENCES company(id),
  tier            INTEGER,
  tier_rationale  TEXT,
  relevance       REAL,
  review_state    TEXT DEFAULT 'pending',
  reviewed_by     TEXT,
  reviewed_at     TEXT,
  review_note     TEXT,
  PRIMARY KEY (segment_slug, company_id)
);

CREATE TABLE IF NOT EXISTS company_field (
  id              INTEGER PRIMARY KEY,
  company_id      INTEGER NOT NULL REFERENCES company(id),
  field           TEXT NOT NULL,
  value           TEXT NOT NULL,
  source_url      TEXT NOT NULL,
  evidence_quote  TEXT,
  confidence      REAL,
  extractor       TEXT NOT NULL,
  extracted_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offering (
  id              INTEGER PRIMARY KEY,
  company_id      INTEGER NOT NULL REFERENCES company(id),
  label           TEXT NOT NULL,
  evidence_url    TEXT NOT NULL,
  evidence_quote  TEXT NOT NULL,
  extracted_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tag (
  company_id      INTEGER NOT NULL REFERENCES company(id),
  facet           TEXT NOT NULL,
  value           TEXT NOT NULL,
  confidence      REAL,
  source_url      TEXT,
  PRIMARY KEY (company_id, facet, value)
);

CREATE TABLE IF NOT EXISTS candidate (
  id              INTEGER PRIMARY KEY,
  segment_slug    TEXT NOT NULL,
  raw_name        TEXT,
  raw_url         TEXT,
  source          TEXT NOT NULL,
  source_detail   TEXT,
  discovered_at   TEXT NOT NULL,
  resolved_to     INTEGER REFERENCES company(id),
  reject_reason   TEXT
);

CREATE TABLE IF NOT EXISTS discovery_run (
  id              INTEGER PRIMARY KEY,
  segment_slug    TEXT NOT NULL,
  source          TEXT NOT NULL,
  query           TEXT,
  results_n       INTEGER,
  new_unique_n    INTEGER,
  cost_usd        REAL,
  started_at      TEXT,
  finished_at     TEXT,
  error           TEXT
);

CREATE TABLE IF NOT EXISTS page (
  url_sha         TEXT PRIMARY KEY,
  company_id      INTEGER REFERENCES company(id),
  url             TEXT NOT NULL,
  content_sha     TEXT,
  http_status     INTEGER,
  fetched_at      TEXT NOT NULL,
  path            TEXT
);

CREATE TABLE IF NOT EXISTS snapshot (
  id              INTEGER PRIMARY KEY,
  segment_slug    TEXT NOT NULL,
  taken_at        TEXT NOT NULL,
  payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_company_domain    ON company(domain);
CREATE INDEX IF NOT EXISTS idx_company_canton    ON company(canton);
CREATE INDEX IF NOT EXISTS idx_membership_lookup ON membership(segment_slug, tier, review_state);
CREATE INDEX IF NOT EXISTS idx_field_lookup      ON company_field(company_id, field);
CREATE INDEX IF NOT EXISTS idx_tag_facet         ON tag(facet, value);
CREATE INDEX IF NOT EXISTS idx_candidate_segment ON candidate(segment_slug, source);
CREATE INDEX IF NOT EXISTS idx_page_company      ON page(company_id);
"""

_MIGRATION_002_FTS: Final = """
CREATE VIRTUAL TABLE IF NOT EXISTS company_fts USING fts5(
  canonical_name, one_liner, offerings_blob,
  content='', tokenize='unicode61 remove_diacritics 2'
);
"""

_MIGRATION_003_CANDIDATE_LOCATION: Final = """
ALTER TABLE candidate ADD COLUMN raw_city TEXT;
ALTER TABLE candidate ADD COLUMN raw_canton TEXT;
"""

_MIGRATION_004_GEOCODE_STATUS: Final = """
ALTER TABLE company ADD COLUMN geocode_status TEXT;
"""

MIGRATIONS: Final[tuple[tuple[int, str, str], ...]] = (
    (1, "core tables and indexes", _MIGRATION_001_CORE),
    (2, "full-text search over searchable company text", _MIGRATION_002_FTS),
    # A hand-curated seed list knows where a firm sits, and that is the only
    # location signal available before any page has been fetched. Without it
    # geocoding cannot run until after extraction, which puts the map — the
    # thing that proves the idea — several stages further away than it needs
    # to be.
    (3, "carry a curator's location knowledge on the candidate", _MIGRATION_003_CANDIDATE_LOCATION),
    # A null lat means two different things — "looked for and not found" and
    # "never looked for" — and the geography check in classify needs to tell
    # them apart. Conflating them excluded 21 companies in Zürich, Geneva and
    # Lugano as foreign, because their tier had kept them out of the geocoding
    # pass entirely.
    (
        4,
        "record whether geocoding was attempted and what it concluded",
        _MIGRATION_004_GEOCODE_STATUS,
    ),
)

SCHEMA_VERSION: Final = max(step[0] for step in MIGRATIONS)


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def connect(path: Path, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """Open the database with the settings this project assumes everywhere.

    ``read_only`` opens via a URI so that a bug in the app layer cannot write.
    Every read path in ``app/`` uses it.
    """
    if read_only:
        if not path.exists():
            msg = f"no database at {path} — run `sectorradar init` first"
            raise FileNotFoundError(msg)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)

    conn.row_factory = sqlite3.Row
    try:
        if not read_only:
            # WAL survives an interrupted run without corrupting the file, which
            # matters because `sectorradar run` must be SIGINT-safe.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
    finally:
        conn.close()


def current_version(conn: sqlite3.Connection) -> int:
    """Return the applied schema version, or 0 on a database with no schema."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    result = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version").fetchone()
    return int(result["v"])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the stored version. Idempotent.

    Returns the number of steps applied, so callers can tell "already current"
    from "just upgraded".
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
          version    INTEGER PRIMARY KEY,
          note       TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    have = current_version(conn)
    applied = 0
    for version, note, ddl in MIGRATIONS:
        if version <= have:
            continue
        conn.executescript(ddl)
        conn.execute(
            "INSERT INTO schema_version (version, note, applied_at) VALUES (?, ?, ?)",
            (version, note, _now()),
        )
        applied += 1
        log.info("migration.applied", version=version, note=note)
    conn.commit()
    return applied


def init_db(path: Path) -> int:
    """Create or upgrade the database at ``path``. Safe to run repeatedly."""
    with connect(path) as conn:
        return migrate(conn)


# --------------------------------------------------------------------------
# Write helpers
# --------------------------------------------------------------------------


def upsert_segment(conn: sqlite3.Connection, slug: str, name: str, config_yaml: str) -> None:
    """Record the segment and the exact YAML it ran with."""
    conn.execute(
        """
        INSERT INTO segment (slug, name, config_yaml, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET name=excluded.name, config_yaml=excluded.config_yaml
        """,
        (slug, name, config_yaml, _now()),
    )


def insert_candidate(
    conn: sqlite3.Connection,
    *,
    segment_slug: str,
    source: str,
    raw_name: str | None,
    raw_url: str | None,
    source_detail: str | None,
    discovered_at: str | None = None,
    raw_city: str | None = None,
    raw_canton: str | None = None,
) -> int:
    """Append a candidate. Returns its row id."""
    cur = conn.execute(
        """
        INSERT INTO candidate
          (segment_slug, raw_name, raw_url, source, source_detail, discovered_at,
           raw_city, raw_canton)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            segment_slug,
            raw_name,
            raw_url,
            source,
            source_detail,
            discovered_at or _now(),
            raw_city,
            raw_canton,
        ),
    )
    return int(cur.lastrowid or 0)


def upsert_company(
    conn: sqlite3.Connection, *, domain: str, canonical_name: str, **fields: Any
) -> int:
    """Insert or update a company keyed on its normalised domain.

    Only non-None values overwrite existing ones, so a later stage with partial
    information cannot blank out what an earlier one established.
    """
    allowed = {
        "uid",
        "legal_name",
        "legal_form",
        "one_liner",
        "street",
        "postal_code",
        "city",
        "canton",
        "country",
        "lat",
        "lon",
        "headcount_est",
        "founded_year",
        "geocode_status",
        "languages",
        "status",
        "last_enriched",
    }
    unknown = set(fields) - allowed
    if unknown:
        msg = f"upsert_company got unknown column(s): {sorted(unknown)}"
        raise ValueError(msg)

    conn.execute(
        """
        INSERT INTO company (domain, canonical_name, first_seen)
        VALUES (?, ?, ?)
        ON CONFLICT(domain) DO NOTHING
        """,
        (domain, canonical_name, _now()),
    )
    row = conn.execute("SELECT id FROM company WHERE domain = ?", (domain,)).fetchone()
    company_id = int(row["id"])

    setters = {k: v for k, v in fields.items() if v is not None}
    if setters:
        assignments = ", ".join(f"{k} = ?" for k in setters)
        # Only column *names* are interpolated, and every one was checked
        # against the `allowed` set above. The values themselves are bound.
        sql = f"UPDATE company SET {assignments} WHERE id = ?"  # noqa: S608
        conn.execute(sql, (*setters.values(), company_id))
    return company_id


def upsert_membership(
    conn: sqlite3.Connection,
    *,
    segment_slug: str,
    company_id: int,
    tier: int | None = None,
    tier_rationale: str | None = None,
    relevance: float | None = None,
) -> None:
    """Attach a company to a segment, preserving any human review already done.

    Once a row has been reviewed, its tier and rationale are the human's and a
    later ``classify`` run must not silently overwrite them — otherwise an hour
    of review is undone by the next pipeline run and nobody notices. Relevance
    is a machine score rather than a decision, so it keeps refreshing.

    To deliberately re-classify a reviewed row, reset its ``review_state`` to
    ``pending`` first.
    """
    conn.execute(
        """
        INSERT INTO membership (segment_slug, company_id, tier, tier_rationale, relevance)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(segment_slug, company_id) DO UPDATE SET
          tier = CASE
                   WHEN COALESCE(membership.review_state, 'pending') != 'pending'
                     THEN membership.tier
                   ELSE COALESCE(excluded.tier, membership.tier)
                 END,
          tier_rationale = CASE
                   WHEN COALESCE(membership.review_state, 'pending') != 'pending'
                     THEN membership.tier_rationale
                   ELSE COALESCE(excluded.tier_rationale, membership.tier_rationale)
                 END,
          relevance = COALESCE(excluded.relevance, membership.relevance)
        """,
        (segment_slug, company_id, tier, tier_rationale, relevance),
    )


def set_review(
    conn: sqlite3.Connection,
    *,
    segment_slug: str,
    company_id: int,
    review_state: str,
    reviewed_by: str,
    review_note: str | None = None,
    tier: int | None = None,
) -> None:
    """Persist a human review decision. The only write the app layer performs."""
    conn.execute(
        """
        UPDATE membership
           SET review_state = ?,
               reviewed_by  = ?,
               reviewed_at  = ?,
               review_note  = COALESCE(?, review_note),
               tier         = COALESCE(?, tier)
         WHERE segment_slug = ? AND company_id = ?
        """,
        (review_state, reviewed_by, _now(), review_note, tier, segment_slug, company_id),
    )
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    """Every table in the database, virtual tables included."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(r["name"]) for r in rows}


def index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {str(r["name"]) for r in rows}
