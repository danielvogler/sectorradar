"""A polite crawler for company websites.

Politeness here is a constraint on the code, not an aspiration:

* ``robots.txt`` is fetched once per host and obeyed.
* Requests to one host are spaced by at least :data:`MIN_INTERVAL` seconds.
* Every request identifies the tool and carries a contact address. If
  ``SECTORRADAR_CONTACT`` is unset the crawler refuses to run at all rather
  than sending an anonymous User-Agent.
* A 403 or a bot-block page means back off and record it. There is no
  workaround, and building one is out of scope.

Raw HTML is written to ``data/raw/<sha256>.html`` and the content hash recorded
on the ``page`` row, so an unchanged page costs nothing to re-process — which is
what makes re-runs cheap enough to do often.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
import urllib.robotparser
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from sectorradar.config import Segment, Settings
from sectorradar.logging import get_logger

log = get_logger(__name__)

#: Seconds between requests to the same host.
MIN_INTERVAL = 1.0
#: Never take more than this many pages from one company.
MAX_PAGES_PER_COMPANY = 8
MAX_RETRIES = 3
TIMEOUT = 20.0
#: Companies crawled at once. The rate limit is per host and a company is one
#: host, so this parallelises across sites without hurrying any single one.
MAX_WORKERS = 8

#: Paths worth trying, in the three languages Swiss firms actually publish in.
CANDIDATE_PATHS: tuple[str, ...] = (
    "/",
    "/services",
    "/leistungen",
    "/angebot",
    "/en/services",
    "/dienstleistungen",
    "/about",
    "/ueber-uns",
    "/about-us",
    "/team",
    "/blog",
    "/insights",
)

#: Phrases that mean "you have been blocked", not "here is the page".
BLOCK_MARKERS: tuple[str, ...] = (
    "access denied",
    "captcha",
    "cloudflare",
    "are you a robot",
    "unusual traffic",
    "request blocked",
)


#: One row destined for the `page` table.
PageRecord = tuple[str, int, str, str, int, str, str]


@dataclass
class CompanyCrawl:
    """What one worker found. Plain data, safe to hand across a thread."""

    pages: list[PageRecord] = field(default_factory=list)
    requested: int = 0
    unchanged: int = 0
    disallowed: int = 0
    blocked: int = 0
    errors: int = 0


@dataclass
class FetchReport:
    companies: int = 0
    requested: int = 0
    stored: int = 0
    unchanged: int = 0
    blocked: int = 0
    disallowed: int = 0
    errors: int = 0
    blocked_hosts: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def url_sha(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def content_sha(body: str) -> str:
    """Hash of the *text*, so a changed ad slot does not look like a changed page."""
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()


def looks_blocked(status: int, body: str) -> bool:
    if status in (401, 403, 429):
        return True
    head = body[:2000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


#: Elements that carry no information about what a company sells.
#:
#: ``footer`` is deliberately **not** in this list. It looks like boilerplate
#: and mostly is, but on a Swiss company site it is also where the postal
#: address lives — and the address is the single most valuable structured fact
#: on the page, because it decides which canton the company is in and whether
#: it is in the country at all.
#:
#: Stripping it was measurably destructive: of the fetched pages that contained
#: a recognisable Swiss address, removing ``footer`` lost every single one, and
#: 61 of 160 enriched companies ended up with no city as a result.
NOISE_TAGS: tuple[str, ...] = ("script", "style", "nav", "noscript", "svg", "form", "iframe")


def main_text(html: str) -> str:
    """Readable text, with scripts and navigation removed but the footer kept."""
    tree = HTMLParser(html)
    for tag in NOISE_TAGS:
        for node in tree.css(tag):
            node.decompose()
    body = tree.body
    text = body.text(separator=" ", strip=True) if body else tree.text(separator=" ", strip=True)
    return " ".join(text.split())


def internal_links(html: str, base_url: str, limit: int = 10) -> list[str]:
    """Same-host links from a page, for walking a blog or insights index."""
    host = urlparse(base_url).hostname or ""
    found: list[str] = []
    seen: set[str] = set()
    for node in HTMLParser(html).css("a[href]"):
        href = node.attributes.get("href")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.hostname != host or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= limit:
            break
    return found


class Politeness:
    """Per-host robots rules and request spacing."""

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}

    def _rules(self, host: str, client: httpx.Client) -> urllib.robotparser.RobotFileParser | None:
        if host in self._robots:
            return self._robots[host]

        parser = urllib.robotparser.RobotFileParser()
        try:
            response = client.get(f"https://{host}/robots.txt", timeout=10.0)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                # No robots.txt is permission, not prohibition.
                parser.parse([])
        except httpx.HTTPError as exc:
            log.warning("fetch.robots_unreachable", host=host, error=str(exc))
            parser.parse([])

        self._robots[host] = parser
        return parser

    def allowed(self, url: str, client: httpx.Client) -> bool:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        rules = self._rules(host, client)
        return True if rules is None else bool(rules.can_fetch(self.user_agent, url))

    def wait(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        last = self._last_request.get(host, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)
        self._last_request[host] = time.monotonic()


def get_with_retries(client: httpx.Client, url: str) -> httpx.Response | None:
    """GET with backoff. Returns None when the URL is not retrievable."""
    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.get(url, timeout=TIMEOUT, follow_redirects=True)
        except httpx.HTTPError as exc:
            log.debug("fetch.request_failed", url=url, attempt=attempt, error=str(exc))
            if attempt == MAX_RETRIES:
                return None
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code >= 500 and attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
            continue
        return response
    return None


def _targets(domain: str) -> list[str]:
    return [f"https://{domain}{path}" for path in CANDIDATE_PATHS]


def _companies_to_fetch(conn: sqlite3.Connection, segment: Segment) -> list[sqlite3.Row]:
    enrich = segment.enrich_tiers or [1, 2]
    placeholders = ",".join("?" * len(enrich))
    sql = f"""
        SELECT c.id, c.domain
          FROM company c
          JOIN membership m ON m.company_id = c.id
         WHERE m.segment_slug = ?
           AND COALESCE(m.review_state, 'pending') != 'rejected'
           AND (m.tier IS NULL OR m.tier IN ({placeholders}))
      ORDER BY c.id
    """  # noqa: S608 - only placeholder counts are interpolated
    return conn.execute(sql, (segment.slug, *enrich)).fetchall()


def fetch(
    conn: sqlite3.Connection,
    segment: Segment,
    settings: Settings,
    *,
    force: bool = False,
    dry_run: bool = False,
    workers: int = MAX_WORKERS,
) -> FetchReport:
    """Crawl each in-scope company's own site, caching raw HTML.

    Companies are crawled concurrently, one worker per company. The politeness
    limit is *per host* and a company is one host, so a worker per company
    honours every individual site's rate limit exactly as the sequential
    version did — it simply stops making one slow server delay every other
    site's crawl. Sequentially this takes about half an hour for 150 companies;
    the wait is almost entirely idle time.

    SQLite writes stay on the calling thread. Workers do network and disk only
    and hand back plain records, which keeps the connection single-threaded
    without a lock.
    """
    user_agent = settings.user_agent()  # raises if SECTORRADAR_CONTACT is unset
    raw_dir = settings.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    report = FetchReport()
    rows = _companies_to_fetch(conn, segment)

    # Read what is already stored once, up front, rather than querying inside
    # each worker — workers must not touch the connection.
    known: set[str] = set()
    if not force:
        known = {
            str(r["url_sha"])
            for r in conn.execute(
                "SELECT url_sha FROM page WHERE content_sha IS NOT NULL"
            ).fetchall()
        }

    jobs = [(int(r["id"]), str(r["domain"])) for r in rows]
    report.companies = len(jobs)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _crawl_company, company_id, domain, known, user_agent, raw_dir, dry_run=dry_run
            ): domain
            for company_id, domain in jobs
        }

        for future in as_completed(futures):
            domain = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                report.errors += 1
                log.warning("fetch.company_failed", domain=domain, error=str(exc))
                continue

            report.requested += result.requested
            report.unchanged += result.unchanged
            report.disallowed += result.disallowed
            report.errors += result.errors
            if result.blocked:
                report.blocked += result.blocked
                if domain not in report.blocked_hosts:
                    report.blocked_hosts.append(domain)

            for record in result.pages:
                conn.execute(
                    """
                    INSERT INTO page
                      (url_sha, company_id, url, content_sha, http_status, fetched_at, path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url_sha) DO UPDATE SET
                      content_sha = excluded.content_sha,
                      http_status = excluded.http_status,
                      fetched_at  = excluded.fetched_at,
                      path        = excluded.path
                    """,
                    record,
                )
                report.stored += 1

            # Commit as each company lands. A crawl of 150 sites runs for
            # minutes even concurrently, and a single commit at the end would
            # mean an interrupt discards all of it — orphaning the HTML already
            # on disk, because the page rows are what link a content hash back
            # to a company.
            if not dry_run:
                conn.commit()

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    log.info(
        "fetch.done",
        segment=segment.slug,
        companies=report.companies,
        stored=report.stored,
        unchanged=report.unchanged,
        blocked=report.blocked,
        disallowed=report.disallowed,
        errors=report.errors,
    )
    return report


def _crawl_company(
    company_id: int,
    domain: str,
    known: set[str],
    user_agent: str,
    raw_dir: Path,
    *,
    dry_run: bool,
) -> CompanyCrawl:
    """Crawl one company. Network and disk only — never touches the database.

    Runs on a worker thread, so it returns records for the caller to write
    rather than writing them itself.
    """
    result = CompanyCrawl()
    politeness = Politeness(user_agent)
    stored_for_company = 0

    with httpx.Client(headers={"User-Agent": user_agent}, follow_redirects=True) as client:
        for url in _targets(domain):
            if stored_for_company >= MAX_PAGES_PER_COMPANY:
                break

            if not politeness.allowed(url, client):
                result.disallowed += 1
                log.debug("fetch.disallowed", url=url)
                continue

            sha = url_sha(url)
            if sha in known:
                result.unchanged += 1
                stored_for_company += 1
                continue

            politeness.wait(url)
            result.requested += 1
            response = get_with_retries(client, url)

            if response is None:
                result.errors += 1
                continue

            if response.status_code == 404:
                continue

            body = response.text
            if looks_blocked(response.status_code, body):
                result.blocked += 1
                log.warning("fetch.blocked", url=url, status=response.status_code)
                # Back off from this host entirely rather than probing further.
                break

            if response.status_code != 200:
                continue

            text = main_text(body)
            if not text:
                continue

            digest = content_sha(text)
            path = raw_dir / f"{digest}.html"
            if not dry_run and not path.exists():
                path.write_text(body, encoding="utf-8")

            result.pages.append(
                (sha, company_id, url, digest, response.status_code, _now(), str(path))
            )
            stored_for_company += 1

    return result


def page_texts(
    conn: sqlite3.Connection, company_id: int, raw_dir: Path
) -> Iterable[tuple[str, str]]:
    """Yield ``(url, cleaned_text)`` for every stored page of a company."""
    # Shortest URL first, which puts the homepage at the front. Callers treat
    # the first page as the company's primary source URL.
    rows = conn.execute(
        "SELECT url, path FROM page WHERE company_id = ? ORDER BY LENGTH(url), url",
        (company_id,),
    ).fetchall()
    for row in rows:
        path = Path(row["path"]) if row["path"] else None
        if path is None or not path.exists():
            continue
        try:
            html = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("fetch.unreadable_page", path=str(path), error=str(exc))
            continue
        yield str(row["url"]), main_text(html)
