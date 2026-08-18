#!/usr/bin/env python3
"""Check the collected data against the promises the tool makes about it.

`make check` proves the code is sound. This proves the *data* is, which is a
different question and the one that actually matters to somebody reading the
output. Every check here corresponds to a claim the interface makes:

* every claim carries evidence that is genuinely on the page it cites
* no company is placed somewhere its address does not support
* no individual person has been recorded
* facet values stay inside the segment's declared vocabulary

Run with ``--live`` to re-fetch a sample of pages and confirm the quotes are
still there. Without it, the checks are offline and instant.

Exits non-zero if anything fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import random
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sectorradar import fetch, swiss
from sectorradar.config import available_segments, load_segment, load_settings
from sectorradar.extract import looks_like_a_person, quote_is_supported

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s()/.-]{7,}\d")

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}{': ' + detail if detail else ''}")
        failures.append(name)


def offline_checks(conn: sqlite3.Connection, slug: str) -> None:
    print(f"\n--- {slug}: evidence ---")

    for table in ("offering", "case_study", "client_reference", "product"):
        empty = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE evidence_quote IS NULL OR evidence_quote = ''"  # noqa: S608
        ).fetchone()[0]
        check(f"every {table} row has a quote", empty == 0, f"{empty} without one")

        no_url = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE evidence_url IS NULL OR evidence_url = ''"  # noqa: S608
        ).fetchone()[0]
        check(f"every {table} row has a source URL", no_url == 0, f"{no_url} without one")

    print(f"\n--- {slug}: personal data ---")

    people = [
        r["client_name"]
        for r in conn.execute("SELECT DISTINCT client_name FROM client_reference").fetchall()
        if looks_like_a_person(str(r["client_name"]))
    ]
    check("no client is an individual", not people, ", ".join(people[:5]))

    leaked = 0
    for table in ("offering", "case_study", "client_reference", "product"):
        for row in conn.execute(f"SELECT evidence_quote FROM {table}").fetchall():  # noqa: S608
            text = str(row["evidence_quote"])
            if EMAIL.search(text) or PHONE.search(text):
                leaked += 1
    check("no evidence quote contains contact details", leaked == 0, f"{leaked} do")

    print(f"\n--- {slug}: geography ---")

    bad_canton = [
        str(r["canton"])
        for r in conn.execute(
            "SELECT DISTINCT canton FROM company WHERE canton IS NOT NULL AND canton != ''"
        ).fetchall()
        if swiss.canton_code(str(r["canton"])) != str(r["canton"])
    ]
    check("every canton is a two-letter code", not bad_canton, ", ".join(bad_canton[:5]))

    placed_but_unfound = conn.execute(
        "SELECT COUNT(*) FROM company WHERE lat IS NOT NULL AND geocode_status = 'not_found'"
    ).fetchone()[0]
    check(
        "nothing is placed on the map that failed to geocode",
        placed_but_unfound == 0,
        f"{placed_but_unfound} are",
    )

    print(f"\n--- {slug}: vocabulary ---")

    segment = load_segment(slug)
    for facet, allowed in segment.facets.items():
        strays = [
            str(r["value"])
            for r in conn.execute(
                "SELECT DISTINCT value FROM tag WHERE facet = ?", (facet,)
            ).fetchall()
            if str(r["value"]) not in allowed
        ]
        check(f"{facet} values stay in the declared vocabulary", not strays, ", ".join(strays[:6]))


def live_checks(conn: sqlite3.Connection, sample: int) -> None:
    """Re-fetch pages and confirm the quotes are still there."""
    import httpx
    from selectolax.parser import HTMLParser

    print(f"\n--- live verification ({sample} quotes) ---")
    settings = load_settings()

    rows = conn.execute(
        """
        SELECT 'offering' AS kind, evidence_url, evidence_quote FROM offering
        UNION ALL SELECT 'case_study', evidence_url, evidence_quote FROM case_study
        UNION ALL SELECT 'client', evidence_url, evidence_quote FROM client_reference
        """
    ).fetchall()
    if not rows:
        check("live sample", False, "nothing to check")
        return

    chosen = random.sample(rows, min(sample, len(rows)))
    verified = unreachable = 0

    with httpx.Client(
        follow_redirects=True, headers={"User-Agent": settings.user_agent()}, timeout=25
    ) as client:
        for row in chosen:
            try:
                html = client.get(str(row["evidence_url"])).text
            except httpx.HTTPError:
                unreachable += 1
                continue
            tree = HTMLParser(html)
            for tag in fetch.NOISE_TAGS:
                for node in tree.css(tag):
                    node.decompose()
            body = tree.body
            text = body.text(separator=" ", strip=True) if body else ""
            verified += quote_is_supported(str(row["evidence_quote"]), text)

    reachable = len(chosen) - unreachable
    ratio = verified / reachable if reachable else 0.0
    # Not 100%: sites change between the crawl and the check, and that is a
    # fact about the world rather than a defect in the extraction.
    check(
        f"live quotes still found ({verified}/{reachable}, {unreachable} unreachable)",
        ratio >= 0.8,
        f"{ratio:.0%} below the 80% floor",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="re-fetch a sample of pages")
    parser.add_argument("--sample", type=int, default=12)
    parser.add_argument(
        "--segment",
        action="append",
        help="Segment slug to check. Repeatable. Defaults to every segment defined.",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.db_path.exists():
        print(f"no database at {settings.db_path} — run `sectorradar init` first")
        return 1

    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row

    print("=" * 58)
    print(" data verification")
    print("=" * 58)

    # Every segment defined, not one named in the source. A check that only
    # ever runs against the example segment stops being a check the moment
    # somebody adds a second one.
    slugs = args.segment or available_segments()
    if not slugs:
        print("no segments defined in segments/ — nothing to verify")
        return 1
    for slug in slugs:
        offline_checks(conn, slug)
    if args.live:
        live_checks(conn, args.sample)

    print("\n" + "=" * 58)
    if failures:
        print(f" {len(failures)} of {checks} checks FAILED")
        for name in failures:
            print(f"   - {name}")
        print("=" * 58)
        return 1
    print(f" all {checks} data checks passed")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
