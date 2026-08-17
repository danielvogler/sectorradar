"""Entity resolution: candidates in, canonical companies out.

This is the stage that decides whether the project works. Discovery producing
duplicates is expected; the database silently containing the same firm three
times, or silently merging two firms that are not the same, is what makes a
market map worthless.

The order of operations, and the reasoning behind each step:

1. **Normalise the domain.** Strip scheme, ``www.``, path and query; lowercase.
   Reject hosts that are never a company's own site — a LinkedIn page is not a
   website, and two firms sharing a Medium URL are not one firm.
2. **Normalise the name.** Strip trailing legal suffixes and fold accents. The
   same firm registered as GmbH in Zug, AG in Zürich and Sàrl in Vaud is one
   firm.
3. **Exact domain match merges.** One website is one company.
4. **Fuzzy name match flags, never merges.** Auto-merging on name similarity is
   how you quietly lose a real competitor, so a near-match becomes a question
   for the human review queue instead.
5. **Everything else is a new company.**
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from sectorradar import db, swiss
from sectorradar.config import Segment
from sectorradar.logging import get_logger

log = get_logger(__name__)

# Similarity at or above which two normalised names are worth a human's glance.
FUZZY_THRESHOLD = 92

# Hosts that are never a company's own website. Social profiles, publishing
# platforms, registries and the agency directories we use as *sources*: a
# directory listing tells you a company exists, it is not that company's site.
EXCLUDED_HOSTS: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "xing.com",
        "youtube.com",
        "medium.com",
        "substack.com",
        "github.io",
        "github.com",
        "gitlab.com",
        "wikipedia.org",
        "crunchbase.com",
        "glassdoor.com",
        "indeed.com",
        "jobs.ch",
        "jobscout24.ch",
        "google.com",
        "goodfirms.co",
        "clutch.co",
        "sortlist.ch",
        "sortlist.com",
        "designrush.com",
        "zefix.ch",
        "moneyhouse.ch",
        "local.ch",
        "sites.google.com",
        "wixsite.com",
        "squarespace.com",
        "notion.site",
        # Listicle and lead-generation sites. These rank well for "top AI
        # companies in <country>" precisely because that is what they are for,
        # so a search-driven source returns them constantly. They are writing
        # *about* the market, not operating in it.
        "techbehemoths.com",
        "aisuperior.com",
        "digiscorp.com",
        "mobian.studio",
        "flypix.ai",
        "aiagencies.eu",
        "themanifest.com",
        "goodfirms.com",
        "topdevelopers.co",
        "itfirms.co",
        "superbcompanies.com",
        "startup-insider.com",
        "wellfound.com",
        "remoterocketship.com",
        "meetfrank.com",
        "lespepitestech.com",
        "ensun.io",
        "swissmadesoftware.org",
    }
)

# Trailing tokens that denote a legal form rather than an identity. Compared
# after accent folding, so "sàrl" and "sarl" both match.
LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "ag",
        "sa",
        "spa",
        "gmbh",
        "sarl",
        "sagl",
        "snc",
        "klg",
        "kmg",
        "llc",
        "llp",
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "se",
        "bv",
        "nv",
        "oy",
        "ab",
        "as",
        "aps",
        "kg",
        "ug",
        "eg",
        "ev",
        "genossenschaft",
        "verein",
        "stiftung",
        "cooperative",
        "scoop",
        "sc",
        "sas",
        "sasu",
    }
)


@dataclass(frozen=True)
class ResolveReport:
    """What one resolve run did. Returned rather than printed, so the CLI owns output."""

    candidates_seen: int = 0
    companies_created: int = 0
    merged_into_existing: int = 0
    rejected: int = 0
    flagged_duplicate: int = 0


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    """Fold to ASCII: ü -> u, é -> e, ß -> ss."""
    text = text.replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _expand_umlauts(text: str) -> str:
    """Fold the German way: ü -> ue, ö -> oe, ä -> ae, ß -> ss."""
    for source, target in (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("Ä", "Ae"),
        ("Ö", "Oe"),
        ("Ü", "Ue"),
        ("ß", "ss"),
    ):
        text = text.replace(source, target)
    return _strip_accents(text)


def normalise_domain(raw: str | None) -> str | None:
    """Reduce a URL to the bare host, or ``None`` if it is not a company site.

    Returning ``None`` rather than raising is deliberate: rejecting a candidate
    is an ordinary outcome of discovery, not an error.
    """
    if not raw or not raw.strip():
        return None

    candidate = raw.strip()
    if "://" not in candidate:
        # A bare "example.ch" is common in hand-written seed lists.
        if candidate.startswith(("mailto:", "tel:", "javascript:")):
            return None
        candidate = f"https://{candidate}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    host = (parsed.hostname or "").lower().strip().removeprefix("www.")
    if not host or "." not in host or " " in host:
        return None
    if host.endswith("."):
        host = host[:-1]

    for excluded in EXCLUDED_HOSTS:
        if host == excluded or host.endswith(f".{excluded}"):
            return None

    return host


def normalise_name(raw: str | None) -> str:
    """Lowercase, drop punctuation, and strip trailing legal-form tokens."""
    if not raw:
        return ""

    text = raw.strip().lower()
    text = "".join(c if (c.isalnum() or c.isspace() or c == "-") else " " for c in text)
    tokens = text.split()

    # Only *trailing* tokens are suffixes: "Sagl" is a legal form, "Sagler" is
    # a surname, and "AG Analytics" is a company whose name starts with AG.
    while len(tokens) > 1 and _strip_accents(tokens[-1]).strip("-") in LEGAL_SUFFIXES:
        tokens.pop()

    return " ".join(tokens)


def _reduce_digraphs(text: str) -> str:
    """Collapse ``ae``/``oe``/``ue`` to bare vowels.

    This is the form in which every spelling of an umlaut finally meets:
    "Zürich" and "Zuerich" both reduce to "zurich", which the two other foldings
    on their own do not achieve — expanding gives "zuerich" for one and leaves
    the other, and stripping gives "zurich" and "zuerich".

    It over-collapses genuine vowel pairs ("Neuenburg" becomes "Nunburg"), but
    the transform is applied to both sides of every comparison, and its only
    consequence is raising a duplicate for a human to glance at. Over-flagging
    costs two seconds; a missed duplicate costs a wrong market map.
    """
    reduced = _expand_umlauts(text)
    for digraph, vowel in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        reduced = reduced.replace(digraph, vowel)
    return reduced


def name_keys(raw: str | None) -> frozenset[str]:
    """Every spelling of a normalised name we are willing to treat as equal.

    Two conventions exist for German umlauts and sources use both: ``ü`` becomes
    ``ue`` in careful writing and ``u`` in careless writing. Emitting the
    expanded, stripped and reduced forms means "Zürich", "Zuerich" and "Zurich"
    all meet on at least one key.
    """
    base = normalise_name(raw)
    if not base:
        return frozenset()
    return frozenset({base, _expand_umlauts(base), _strip_accents(base), _reduce_digraphs(base)})


def looks_like_duplicate(
    left: str | None, right: str | None, threshold: int = FUZZY_THRESHOLD
) -> bool:
    """Whether two names are close enough to be worth a human's attention."""
    left_keys, right_keys = name_keys(left), name_keys(right)
    if not left_keys or not right_keys:
        return False
    if left_keys & right_keys:
        return True
    return any(fuzz.ratio(a, b) >= threshold for a in left_keys for b in right_keys)


def canonical_name_for(raw_name: str | None, domain: str) -> str:
    """A display name, falling back to the domain when a source gave none."""
    if raw_name and raw_name.strip():
        return raw_name.strip()
    stem = domain.split(".")[0]
    return stem.replace("-", " ").title().replace(" ", "-") if "-" in stem else stem.title()


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def _existing_companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, domain, canonical_name, canton FROM company").fetchall()


def _find_duplicate_candidate(
    name: str | None,
    canton: str | None,
    known: list[tuple[int, str, str, str | None]],
) -> str | None:
    """Return the domain of a probable duplicate, or None.

    The canton constraint from the specification only narrows the comparison
    when both cantons are known. At resolve time they usually are not — canton
    arrives later, from extraction and geocoding — so an unknown canton means
    "compare anyway" rather than "skip". Flagging costs a human two seconds;
    missing a duplicate costs a wrong market map.
    """
    for _, other_domain, other_name, other_canton in known:
        if canton and other_canton and canton != other_canton:
            continue
        if looks_like_duplicate(name, other_name):
            return other_domain
    return None


def resolve(conn: sqlite3.Connection, segment: Segment, *, dry_run: bool = False) -> ResolveReport:
    """Turn every unresolved candidate for ``segment`` into a company row.

    Idempotent: a candidate that already carries a ``resolved_to`` or a
    ``reject_reason`` is skipped, so re-running costs nothing and changes
    nothing.
    """
    # membership.segment_slug is a foreign key, so the segment row has to exist
    # before any company can be attached to it. Recording it here also stores
    # the exact configuration this run used.
    db.upsert_segment(conn, segment.slug, segment.name, segment.to_yaml())

    rows = conn.execute(
        """
        SELECT id, raw_name, raw_url, raw_city, raw_canton
          FROM candidate
         WHERE segment_slug = ?
           AND resolved_to IS NULL
           AND reject_reason IS NULL
      ORDER BY id
        """,
        (segment.slug,),
    ).fetchall()

    known = [
        (int(r["id"]), str(r["domain"]), str(r["canonical_name"]), r["canton"])
        for r in _existing_companies(conn)
    ]
    by_domain = {domain: company_id for company_id, domain, _, _ in known}

    seen = created = merged = rejected = flagged = 0

    for row in rows:
        seen += 1
        domain = normalise_domain(row["raw_url"])

        if domain is None:
            reason = (
                "no usable URL"
                if not row["raw_url"]
                else f"not a company website: {row['raw_url']}"
            )
            conn.execute("UPDATE candidate SET reject_reason = ? WHERE id = ?", (reason, row["id"]))
            rejected += 1
            log.debug("resolve.rejected", candidate=row["id"], reason=reason)
            continue

        name = row["raw_name"]

        if domain in by_domain:
            company_id = by_domain[domain]
            merged += 1
            # A later seed for a known domain may carry location the first did not.
            db.upsert_company(
                conn,
                domain=domain,
                canonical_name=canonical_name_for(name, domain),
                city=row["raw_city"],
                canton=swiss.canton_code(row["raw_canton"]),
            )
        else:
            duplicate_of = _find_duplicate_candidate(name, row["raw_canton"], known)
            company_id = db.upsert_company(
                conn,
                domain=domain,
                canonical_name=canonical_name_for(name, domain),
                city=row["raw_city"],
                canton=swiss.canton_code(row["raw_canton"]),
            )
            by_domain[domain] = company_id
            known.append((company_id, domain, canonical_name_for(name, domain), row["raw_canton"]))
            created += 1

            db.upsert_membership(conn, segment_slug=segment.slug, company_id=company_id)

            if duplicate_of is not None:
                flagged += 1
                conn.execute(
                    """
                    UPDATE membership
                       SET review_state = 'needs_info',
                           review_note  = ?
                     WHERE segment_slug = ? AND company_id = ?
                    """,
                    (
                        f"auto: possibly the same firm as {duplicate_of} — "
                        "names are near-identical on different domains",
                        segment.slug,
                        company_id,
                    ),
                )
                log.info("resolve.possible_duplicate", domain=domain, other=duplicate_of)

        db.upsert_membership(conn, segment_slug=segment.slug, company_id=company_id)
        conn.execute("UPDATE candidate SET resolved_to = ? WHERE id = ?", (company_id, row["id"]))

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    report = ResolveReport(
        candidates_seen=seen,
        companies_created=created,
        merged_into_existing=merged,
        rejected=rejected,
        flagged_duplicate=flagged,
    )
    log.info(
        "resolve.done",
        segment=segment.slug,
        seen=seen,
        created=created,
        merged=merged,
        rejected=rejected,
        flagged=flagged,
        dry_run=dry_run,
    )
    return report
