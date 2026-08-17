# sectorradar — Implementation Handoff

**Status:** design complete, repository empty (README + .gitignore only).
**Audience:** an autonomous agent session that will build this to completion without further human input.
**Owner context:** Vogler Consulting (vogler-consulting.ch), Zürich. Agentic AI + GenAI enablement. First real use of the tool is mapping his own competitive landscape in Switzerland.

---

# PART I — HOW TO EXECUTE THIS DOCUMENT

Read Part I fully before touching a file. It is the operating contract; Parts II–IV are the specification.

## 0.1 Mission

Build `sectorradar` from nothing to a working, publishable, professionally packaged v1, following the specification in Parts II–IV, in the phase order in Part IV. Do not stop at a phase boundary. Do not ask for permission between phases. Stop only when the completion promise in §0.4 is literally true, or when you hit one of the hard blockers in §0.7.

## 0.2 Rules of engagement

1. **Work in dependency order, phase by phase.** Each phase in Part IV has a hard acceptance gate with runnable verification commands. A phase is not done until its gate command exits 0 and you have pasted its output into the ledger.
2. **Commit at every green gate**, and at meaningful sub-steps within a phase. Small commits — a stalled session should lose minutes, not hours.
3. **Never commit a broken tree.** `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` must pass before every commit. Pre-commit enforces most of it; do not use `--no-verify` under any circumstance.
4. **Never commit secrets or collected data.** `data/` and `.env` are gitignored and must stay that way. See §14 and `DATA.md`.
5. **No `Co-Authored-By:` trailers for AI agents on any commit.** See §0.6.
6. **Tests before implementation** for `resolve.py` and `extract.py` specifically — those are the two modules where correctness is not self-evident. Elsewhere, tests alongside is fine.
7. **When the spec and reality disagree, reality wins** — but record the divergence in the ledger under `## Deviations` with one sentence of rationale. Do not silently redesign.
8. **Prefer boring.** No new frameworks beyond those named in §2. If a dependency is not listed and you want it, justify it in the ledger in one line and add it.
9. **Budget guard.** Do not spend more than **USD 25** of API credit across the whole build. Track spend in the ledger. If you approach it, stop enrichment work and finish the remaining non-API phases.
10. **Live-network work is allowed and expected** (crawling, search APIs, LLM calls) within the politeness constraints of §14. Anything destructive — force-push, history rewrite, deleting `data/`, publishing the repo to GitHub — is out of scope. Do not do it.

## 0.3 Progress ledger — read this first, write it every iteration

Maintain `notes/PROGRESS.md`. **On session start, read it before anything else** — it, not this file, tells you where you are. If it does not exist, create it from the template below and start at Phase 0.

```markdown
# sectorradar — build progress

**Last updated:** <ISO 8601 timestamp>
**Current phase:** <0–7>
**Spend so far (USD):** <number>

## Gate status
- [ ] Phase 0 — repository scaffold & tooling
- [ ] Phase 1 — schema, config, CLI skeleton
- [ ] Phase 2 — manual spine (seeds → resolve → geocode → map)
- [ ] Phase 3 — enrichment (fetch → extract → classify → review UI)
- [ ] Phase 4 — discovery (websearch → jobads → directories → stats)
- [ ] Phase 5 — long tail (LINDAS, snapshots, export, second segment)
- [ ] Phase 6 — hardening (coverage, CI green, docs)
- [ ] Phase 7 — release readiness

## Log
### <timestamp> — Phase N
What I did. What the gate command printed. What is next.

## Deviations
- <spec said X, I did Y, because Z>

## Open items for the owner
- <anything only a human can decide — do NOT block on these, note and continue>
```

Append to `## Log` every time you complete a gate or make a non-obvious decision. Keep entries short. This file is the only thing standing between a context reset and a rebuild from scratch.

## 0.4 Completion condition

This build is launched with `/goal`, where a **separate evaluator** decides whether the goal is met. Your own assertion that you are finished carries no weight. Therefore the completion condition is a command, not a claim:

> **`make verify` exits 0, and `notes/PROGRESS.md` shows all seven gate checkboxes ticked.**

`make verify` is defined in Phase 0f and runs every phase gate in sequence. **Build it in Phase 0, before you have anything to verify** — it is the evaluator's only handle on your work, and a goal condition that cannot be checked mechanically will simply never be confirmed.

Do not weaken `make verify` to make it pass. If a gate fails, fix the code. Removing a check from the target is the one failure mode that turns this whole document into theatre, and the evaluator reads the Makefile.

If you are stuck, write the blocker into `## Open items for the owner`, take the next unblocked task, and keep going.

For a `/ralph-loop` launch instead (see §0.8), the equivalent completion promise string is:

> `SECTORRADAR V1 COMPLETE: make verify exits 0 and notes/PROGRESS.md records every gate with its verification output.`

Output it verbatim and alone on a line, only when both clauses are unequivocally true, and never to escape a stuck state.

## 0.5 Definition of done (the full checklist)

The build is complete when all of the following hold:

**Tooling and hygiene**
- [ ] `uv sync` reproduces the environment; `uv.lock` is committed
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass clean
- [ ] `uv run mypy` passes with `strict = true` over `src/`
- [ ] `uv run pytest --cov` passes, coverage ≥ 80% on `src/`
- [ ] `uv run pre-commit run --all-files` passes clean
- [ ] `gitleaks` finds nothing, in pre-commit and in CI
- [ ] CI is green on `main` for the full matrix

**Repository presentation**
- [ ] `README.md` with badges, a Mermaid architecture diagram, quickstart, and a "Scope and limitations" section
- [ ] `AGENTS.md` as the single source of truth for agent and human contributors
- [ ] `CLAUDE.md` and `GEMINI.md` exist and are one-line pointers to `AGENTS.md`
- [ ] `LICENSE` (Apache-2.0), `NOTICE`, `DATA.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`
- [ ] No commit in the history carries an AI `Co-Authored-By:` trailer

**Function**
- [ ] `sectorradar run --segment agentic-ai-ch` completes end to end on a cold database
- [ ] ≥ 150 companies in the DB, of which ≥ 100 are tier 1 or 2 with a non-null `tier_rationale`
- [ ] Gold-set recall ≥ 80%, reported by `sectorradar stats`
- [ ] `streamlit run app/Home.py` serves Home, Map, Table, Review and Company pages against the real DB
- [ ] A second segment works from one new YAML file with no code change

## 0.6 Commit discipline

- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`.
- Subject ≤ 72 chars, imperative mood. Body explains *why*, not *what*.
- **No `Co-Authored-By:` trailer naming Claude, Anthropic, Gemini, Codex, Copilot or any other agent.** Three layers enforce this; install all three in Phase 0:
  1. `~/.claude/settings.json` already has `"includeCoAuthoredBy": false` — verify, do not assume.
  2. A `commit-msg` pre-commit hook that rejects the trailer (config in Phase 0).
  3. Before declaring done, run `git log --format='%B' | grep -i 'co-authored-by'` and confirm it is empty.
- Branch: work directly on `main` in this repo. It is unpublished and single-author; a PR flow adds ceremony without a reviewer.
- Tag `v0.1.0` at the end of Phase 7. Do not push tags anywhere.

## 0.7 Hard blockers — stop and write to the ledger

Stop the loop and surface these only if they occur; everything else is a task, not a blocker.

- No API key available for the LLM provider **and** no key for any search provider (Phases 3–4 cannot proceed; finish everything else first, then stop).
- `uv` not installed. Report the install command; do not fall back to pip — the lockfile is the point.
- A required upstream service is down for more than one full retry cycle across two attempts spaced ≥ 30 minutes.

## 0.8 How this document gets launched

**Primary — `/goal` (built in to Claude Code).** It sets a session goal condition and keeps the session working until a separate evaluator confirms the condition is met.

```
/goal Build sectorradar to v1 by executing notes/sectorradar-handoff.md. Read notes/PROGRESS.md first and resume from the first unchecked gate. Follow Part I exactly: commit at every green gate, no AI co-author trailers, never --no-verify. Done when `make verify` exits 0 and all seven gate checkboxes in notes/PROGRESS.md are ticked.
```

Two preconditions, both of which hold in this workspace but should be re-checked if the environment moves:

- `/goal` runs **only in a trusted workspace**. If the trust dialog has not been accepted for this directory, `/goal` refuses; restart and accept it.
- `/goal` refuses to run **while hooks are restricted** — `disableAllHooks` or `allowManagedHooksOnly` in settings or by managed policy. Neither may be set.

Because the evaluator is separate from the working session, everything in §0.5 must be checkable from the repository: a passing command, a file that exists, a `git log` that is empty. Anything whose only evidence is your own narration does not count towards the goal. This is why `make verify` exists, and why the ledger records gate *output* rather than gate *claims*.

**Fallback — `/ralph-loop`** (installed from the official plugin marketplace), which re-feeds the same prompt on exit and trusts a completion-promise string:

```
/ralph-loop "Read notes/sectorradar-handoff.md and notes/PROGRESS.md. Continue the build from the first unchecked gate. Follow Part I exactly." --completion-promise "SECTORRADAR V1 COMPLETE: make verify exits 0 and notes/PROGRESS.md records every gate with its verification output."
```

**Fallback — plain prompt**, no loop, for a single long session:

```
Read notes/sectorradar-handoff.md in full, then notes/PROGRESS.md. Execute Part IV from the first unchecked gate to the end. Do not stop between phases.
```

All three routes rely on the same property: the document is self-contained. An implementing session needs nothing but this file and the repository.

---

# PART II — WHAT THIS IS

## 1. Product

A local-first tool that builds and maintains a **structured, evidence-backed database of every company in a defined market segment within a defined geography**, then lets you browse, filter, and map it.

Two halves, strictly separated:

- **`src/sectorradar/`** — the pipeline. Discovers candidate companies from multiple channels, dedupes them, crawls their sites, extracts structured profiles with an LLM, classifies and geocodes them, writes to SQLite.
- **`app/`** — a Streamlit app. Reads that SQLite file. Table view, map view, faceted filters, per-company detail, and a human review queue.

First segment: **agentic AI / GenAI-enablement service providers in Switzerland.** But the segment definition is a YAML file, not code. A second segment must cost one file, not a refactor.

### Non-goals for v1

- No cloud deployment. Runs on a laptop, `streamlit run app/Home.py`.
- No Postgres, no PostGIS, no pgvector. SQLite only.
- No outreach, CRM, email, or contact-person data.
- No scheduled/continuous monitoring. Manual `sectorradar` CLI runs.
- No multi-user auth.

Cloud Run + Cloud SQL is a plausible v2. Design so that lifting `src/` into a container and repointing `app/` at a hosted DB requires no rewrite — but do not build for it now.

## 2. Decisions already made (do not relitigate)

| Decision | Rationale |
|---|---|
| Name: `sectorradar` (no separator, no hyphen) | One spelling across repo / PyPI / import / CLI |
| SQLite, single file at `data/radar.db` | Target scale is 100–2000 rows, not 10M |
| `data/radar.db` is the **only** interface between `src/` and `app/` | App must never crawl or call an LLM on a Streamlit rerun |
| Python 3.11+, `uv` for deps and locking | Team convention; commit `uv.lock` |
| `ruff` for lint **and** format; `mypy --strict`; `pytest` | No black, no isort, no flake8 |
| `typer` for CLI, `pydantic` v2 for schemas, `structlog` for logs | Standard, boring |
| Every extracted claim carries a source URL + timestamp | An unverifiable competitor DB is worse than none |
| Segment config in YAML under `segments/` | Genericity mechanism |
| Human review is a first-class pipeline stage | At 200 rows you can eyeball 100% — that's the main quality lever |
| Licence: Apache-2.0 | Patent grant matters here; see §17 |

### Naming conventions

- Python package / repo / CLI: `sectorradar`
- Segment slugs (filenames, DB keys): kebab-case — `agentic-ai-ch`

## 3. Critical external-service facts (verified Aug 2026 — re-verify before coding)

These were wrong in an earlier draft. Use these.

**Zefix REST API**
- Host is `www.zefix.admin.ch` — the bare `zefix.admin.ch` has **no DNS record** and will fail.
- Base: `https://www.zefix.admin.ch/ZefixPublicREST/api/v1`
- Swagger: `https://www.zefix.admin.ch/ZefixPublicREST/swagger-ui/index.html`
- Endpoints: `POST /company/search`, `GET /company/uid/{id}`, `GET /company/chid/{id}`, `GET /legalForm`, `GET /community`, SOGC endpoints.
- **Requires credentials.** Request by emailing `zefix@bj.admin.ch`.
- **Search is by company NAME, not by purpose text.** You cannot sweep it by business activity.
- → **Skip this for v1.** The auth friction isn't worth it when search is name-only.

**LINDAS SPARQL (this is the one to use)**
- Query endpoint: `https://ld.admin.ch/query` — POST, `Content-Type: application/x-www-form-urlencoded; charset=utf-8`, `Accept: text/csv`
- Zefix-specific endpoint also exists: `https://register.ld.admin.ch/sparql/`
- Browser UI for building queries: `https://lindas.admin.ch/sparql` (YASGUI; has a "share → curl" button)
- The backing store is Stardog and **supports full-text search via SPARQL** — this is what makes the *Zweck* (company purpose) sweep possible.
- Contains: legal entity name, legal form, purpose text, registered seat, domicile address, UID, status.
- **NOGA industry codes are no longer publicly exposed** (legal practice change at BFS). You must classify yourself. Do not build anything that depends on NOGA.

**Do NOT use**
- `ld.geo.admin.ch/query` — stale, from a 2021 notebook, TLS fails.
- BFS BurWeb — restricted to authorities, contractually priced.

**Geocoding** — geocode only the addresses you keep (~200), so anything free works:
- swisstopo: `https://api3.geo.admin.ch/rest/services/api/SearchServer?type=locations&searchText=...`
- Fallback: Nominatim, 1 req/s, set a real User-Agent.
- Cache every result to disk keyed on the normalised address string. Never geocode twice.

## 4. The segment being built first

**`segments/agentic-ai-ch.yaml`** — agentic AI and GenAI-enablement service providers in Switzerland.

**Expected scale (estimates, validate against reality):**

| Tier | Definition | Expected count |
|---|---|---|
| 1 | Agentic AI is the primary offering; agents/LLM systems are the product being sold as a service | 30–60 |
| 2 | Broader AI/data consultancy that also ships agents or runs GenAI workshops | 100–200 |
| 3 | General digital agency or system integrator with an AI service line | 500–1500 (mostly noise) |
| 4 | Training providers and solo consultants doing GenAI workshops | separate set, relevant to the workshop half of the business |

**v1 target: complete T1 + T2 (~150–250 rows).** Capture T3 as candidates but leave them unenriched by default.

The hard part is **not** the search — it's the boundary. Therefore: `tier` and `tier_rationale` are mandatory non-null fields on every included company, and the inclusion rule lives in the YAML as prose that gets injected verbatim into the classifier prompt.

---

# PART III — TECHNICAL SPECIFICATION

## 5. Repo layout

```
sectorradar/
├── pyproject.toml
├── uv.lock                     # committed
├── .python-version             # 3.11
├── README.md
├── AGENTS.md                   # contributor + agent contract (source of truth)
├── CLAUDE.md                   # one-line pointer -> AGENTS.md
├── GEMINI.md                   # one-line pointer -> AGENTS.md
├── LICENSE                     # Apache-2.0
├── NOTICE
├── DATA.md                     # data licensing & source terms
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md                # Keep a Changelog format
├── CODE_OF_CONDUCT.md
├── .env.example
├── .gitignore                  # data/ , .env , .venv
├── .gitattributes
├── .editorconfig
├── .pre-commit-config.yaml
├── .gitleaks.toml              # only if a documented false positive needs allowlisting
├── Makefile                    # setup / lint / typecheck / test / run / app
├── .github/
│   ├── workflows/ci.yml
│   ├── dependabot.yml
│   ├── CODEOWNERS
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/{bug_report.md,feature_request.md,config.yml}
├── docs/
│   ├── architecture.md         # the Mermaid diagrams and the why
│   ├── adding-a-segment.md     # the genericity contract, worked example
│   └── operations.md           # cost, rate limits, re-run semantics
├── segments/
│   ├── agentic-ai-ch.yaml
│   └── genai-training-ch.yaml  # Phase 5, proves the abstraction
├── src/sectorradar/
│   ├── __init__.py
│   ├── py.typed                # ships type information
│   ├── config.py               # loads .env + segment YAML into pydantic models
│   ├── logging.py              # structlog config, called once from cli.py
│   ├── db.py                   # schema DDL, migrations, upsert helpers
│   ├── models.py               # pydantic: CompanyProfile, Offering, Candidate
│   ├── sources/
│   │   ├── __init__.py         # SOURCES registry: name -> callable
│   │   ├── lindas.py           # SPARQL purpose sweep
│   │   ├── websearch.py        # Exa / Brave / Tavily behind one interface
│   │   ├── directories.py      # goodfirms, designrush, clutch, sortlist
│   │   ├── jobads.py           # jobs.ch keyword search
│   │   └── seeds.py            # hand-curated URL list from the YAML
│   ├── discover.py             # run sources -> candidate rows + run stats
│   ├── resolve.py              # normalise + dedupe -> canonical company rows
│   ├── fetch.py                # polite crawler, caches raw HTML
│   ├── extract.py              # LLM -> CompanyProfile
│   ├── classify.py             # tier + facets + rationale
│   ├── geocode.py
│   ├── stats.py                # saturation curve, coverage, cost
│   ├── export.py
│   └── cli.py                  # typer app
├── app/
│   ├── Home.py                 # overview + segment picker + counts
│   ├── pages/
│   │   ├── 1_Map.py
│   │   ├── 2_Table.py
│   │   ├── 3_Review.py
│   │   └── 4_Company.py
│   └── lib/
│       ├── queries.py          # ALL SQL lives here
│       └── filters.py          # shared sidebar filter widget
├── data/                       # gitignored
│   ├── radar.db
│   ├── raw/                    # <sha256>.html
│   ├── cache/                  # geocode.json, search responses
│   └── exports/
├── notes/
│   ├── sectorradar-handoff.md  # this file
│   └── PROGRESS.md             # the ledger
└── tests/
    ├── conftest.py
    ├── test_architecture.py    # app/ must not import src/sectorradar/
    ├── test_resolve.py         # dedupe cases — the highest-value tests
    ├── test_extract.py         # schema conformance + evidence-substring rule
    ├── test_db.py
    ├── test_config.py
    └── fixtures/
```

**Enforce in `tests/test_architecture.py`:** `app/` must not import from `src/sectorradar/`, and no module under `src/` may import `streamlit`. Walk the AST of every file; do not grep.

## 6. Database schema (SQLite)

Two design rules drive this:

1. **Provenance per claim.** `company_field` is one row per extracted value with a source URL — not a wide table. When the UI says a competitor runs GenAI workshops, you must be able to click through to the sentence that says so.
2. **Snapshots.** The interesting question in month three is "who's new / who repositioned". You cannot reconstruct that from a mutable table.

```sql
CREATE TABLE segment (
  slug            TEXT PRIMARY KEY,        -- 'agentic-ai-ch'
  name            TEXT NOT NULL,
  config_yaml     TEXT NOT NULL,           -- full YAML as stored at last run
  created_at      TEXT NOT NULL
);

CREATE TABLE company (
  id              INTEGER PRIMARY KEY,
  uid             TEXT UNIQUE,             -- CHE-XXX.XXX.XXX, nullable
  domain          TEXT UNIQUE NOT NULL,    -- normalised: no scheme, no www, no path
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
  languages       TEXT,                    -- JSON array
  status          TEXT,                    -- active | liquidation | unknown
  first_seen      TEXT NOT NULL,
  last_enriched   TEXT
);

CREATE TABLE membership (
  segment_slug    TEXT NOT NULL REFERENCES segment(slug),
  company_id      INTEGER NOT NULL REFERENCES company(id),
  tier            INTEGER,                 -- 1|2|3|4, NULL = unclassified
  tier_rationale  TEXT,
  relevance       REAL,                    -- 0..1
  review_state    TEXT DEFAULT 'pending',  -- pending|accepted|rejected|needs_info
  reviewed_by     TEXT,
  reviewed_at     TEXT,
  review_note     TEXT,
  PRIMARY KEY (segment_slug, company_id)
);

CREATE TABLE company_field (
  id              INTEGER PRIMARY KEY,
  company_id      INTEGER NOT NULL REFERENCES company(id),
  field           TEXT NOT NULL,           -- 'headcount_est', 'founded_year', ...
  value           TEXT NOT NULL,
  source_url      TEXT NOT NULL,
  evidence_quote  TEXT,                    -- <= 15 words, verbatim
  confidence      REAL,
  extractor       TEXT NOT NULL,           -- 'extract.v1/claude-haiku-4.5'
  extracted_at    TEXT NOT NULL
);

CREATE TABLE offering (
  id              INTEGER PRIMARY KEY,
  company_id      INTEGER NOT NULL REFERENCES company(id),
  label           TEXT NOT NULL,
  evidence_url    TEXT NOT NULL,
  evidence_quote  TEXT NOT NULL,           -- <= 15 words, verbatim
  extracted_at    TEXT NOT NULL
);

CREATE TABLE tag (
  company_id      INTEGER NOT NULL REFERENCES company(id),
  facet           TEXT NOT NULL,           -- service_type|delivery_model|vertical|tech
  value           TEXT NOT NULL,
  confidence      REAL,
  source_url      TEXT,
  PRIMARY KEY (company_id, facet, value)
);

CREATE TABLE candidate (
  id              INTEGER PRIMARY KEY,
  segment_slug    TEXT NOT NULL,
  raw_name        TEXT,
  raw_url         TEXT,
  source          TEXT NOT NULL,           -- 'websearch'|'lindas'|'directories'|...
  source_detail   TEXT,                    -- the query or page it came from
  discovered_at   TEXT NOT NULL,
  resolved_to     INTEGER REFERENCES company(id),
  reject_reason   TEXT                     -- set when resolve.py discards it
);

CREATE TABLE discovery_run (
  id              INTEGER PRIMARY KEY,
  segment_slug    TEXT NOT NULL,
  source          TEXT NOT NULL,
  query           TEXT,
  results_n       INTEGER,
  new_unique_n    INTEGER,                 -- feeds the saturation curve
  cost_usd        REAL,
  started_at      TEXT,
  finished_at     TEXT,
  error           TEXT
);

CREATE TABLE page (
  url_sha         TEXT PRIMARY KEY,
  company_id      INTEGER REFERENCES company(id),
  url             TEXT NOT NULL,
  content_sha     TEXT,                    -- skip re-extraction if unchanged
  http_status     INTEGER,
  fetched_at      TEXT NOT NULL,
  path            TEXT                     -- data/raw/<sha>.html
);

CREATE TABLE snapshot (
  id              INTEGER PRIMARY KEY,
  segment_slug    TEXT NOT NULL,
  taken_at        TEXT NOT NULL,
  payload         TEXT NOT NULL            -- JSON dump of the accepted set
);

-- FTS over the text you'll actually search in the app
CREATE VIRTUAL TABLE company_fts USING fts5(
  canonical_name, one_liner, offerings_blob,
  content='', tokenize='unicode61 remove_diacritics 2'
);
```

Indexes: `company(domain)`, `company(canton)`, `membership(segment_slug, tier, review_state)`, `company_field(company_id, field)`, `tag(facet, value)`.

**Migrations:** a `schema_version` table and a list of ordered DDL steps in `db.py`. Not Alembic — one file, one dependency-free upgrade path.

## 7. Pipeline stages and module contracts

```
discover → resolve → fetch → extract → classify → geocode → review → snapshot
```

Every stage is independently re-runnable and idempotent. Each writes to the DB; none holds state in memory across a run.

### `discover.py`
`discover(segment, sources: list[str] | None) -> DiscoveryReport`

Runs each enabled source, writes `candidate` rows and a `discovery_run` row per query. Sources share one interface:

```python
def run(segment: Segment, ctx: Ctx) -> Iterator[Candidate]: ...
```

Track `new_unique_n` per query. **Saturation rule:** when 10 consecutive queries within one source yield <5% new unique candidates, log a warning and stop that source. Don't rephrase — switch channel.

### `resolve.py`
The stage that decides whether this project works. Order of operations:

1. Normalise the domain — resolve redirects, strip scheme/`www.`/path/query, lowercase, drop obvious non-company hosts (linkedin.com, facebook.com, medium.com, github.io, wikipedia.org).
2. Normalise the name — strip legal suffixes (AG, GmbH, Sàrl, SA, Sagl, LLC, Ltd), fold umlauts (ä→ae, ö→oe, ü→ue **and** ä→a — try both), collapse whitespace, lowercase.
3. Exact match on domain → merge.
4. Fuzzy match on normalised name (rapidfuzz, threshold ~92) **within the same canton** → flag for human confirmation, don't auto-merge.
5. Everything else → new company row.

**Swiss-specific traps — write a test for each before writing the module:**
- Same firm as "Vogler Consulting", "Vogler Consulting GmbH", "Vogler Consulting Sàrl" across cantons.
- Umlaut variants: "Zürich" / "Zurich" / "Zuerich".
- Multi-language duplicates from LINDAS (DE/FR/IT names for one entity).
- Holding + operating company sharing one website.
- Agency and its product spinoff on the same domain.

### `fetch.py`
Politeness is non-optional: respect `robots.txt`, 1–2 req/s per host, real User-Agent identifying the tool with a contact address (read from `SECTORRADAR_CONTACT` in `.env`; refuse to crawl if unset), 3 retries with backoff.

Fetch per company: `/`, `/services` (+ `/leistungen`, `/angebot`, `/en/services`), `/about` (+ `/ueber-uns`, `/team`), `/blog` or `/insights` (index only, first 10 links). Cap at 8 pages/company. Store raw HTML to `data/raw/<sha256>.html`, record `content_sha` in `page`. **If `content_sha` is unchanged since last run, skip extraction entirely.**

Use `httpx` + `selectolax` or `trafilatura` for main-content extraction. Only reach for a headless browser if a specific site demands it — most Swiss agency sites are static enough.

### `extract.py`
LLM call per company over concatenated cleaned page text. Structured output against `CompanyProfile` (§8). Small/cheap model is sufficient. Rules for the prompt:

- Every `Offering` and every optional scalar must carry `evidence_url` + a verbatim `evidence_quote` of **at most 15 words**.
- Emitting `null` must be explicitly framed as the correct answer when the site doesn't say. Otherwise you get confident fabricated service lists.
- Post-validate: assert each `evidence_quote` is a genuine substring of the fetched text (whitespace-normalised comparison). Drop the claim if not, and count the drop into a per-run `hallucination_rate` logged to the ledger. This catches the majority of hallucinations for free.

Prompts live in `src/sectorradar/prompts/*.md` and are versioned — the `extractor` column records `<prompt-version>/<model-id>` so a re-run with a new prompt is distinguishable from the old one.

### `classify.py`
Separate call from extraction, because tiering depends on the segment definition and you'll iterate on it independently. Injects the segment YAML's `inclusion` prose and `tiers` map verbatim into the prompt. Emits `tier`, `tier_rationale`, `relevance`, and facet tags.

**Facets are fixed, values are open.** The model may emit new values within a known facet; it may not invent facets. Periodically cluster the free values and promote frequent ones into the YAML's controlled vocabulary.

### `geocode.py`
Cache-first, on `data/cache/geocode.json`. Only geocodes companies with `review_state='accepted'` or `tier<=2` — don't burn requests on the T3 pool.

### `stats.py`
- Saturation curve per source (new_unique over query sequence) — plotted in the app.
- Coverage against a **gold set**: a hand-written list of Swiss firms the owner already knows compete with him, stored in the segment YAML. Every run reports recall against it. **This is the single most important number in the project** and the thing that gets skipped. Without it you'll tune prompts on vibes for a fortnight.
- Cost per run from `discovery_run.cost_usd` + token accounting.

## 8. Pydantic contracts

```python
from pydantic import BaseModel, HttpUrl, Field
from typing import Literal

class Offering(BaseModel):
    label: str
    evidence_quote: str = Field(max_length=120)   # <= ~15 words, verbatim
    evidence_url: HttpUrl

class CompanyProfile(BaseModel):
    legal_name: str | None = None
    domain: str
    one_liner: str
    offerings: list[Offering] = []
    facets: dict[str, list[str]] = {}
    headcount_estimate: int | None = None
    founded_year: int | None = None
    languages: list[str] = []
    city: str | None = None
    canton: str | None = None
    confidence: float = Field(ge=0, le=1)

class Classification(BaseModel):
    tier: Literal[1, 2, 3, 4] | None
    tier_rationale: str
    relevance: float = Field(ge=0, le=1)
    facets: dict[str, list[str]] = {}
```

## 9. Segment YAML

`segments/agentic-ai-ch.yaml` — the genericity mechanism. Everything segment-specific lives here. Validated on load by a pydantic `Segment` model; an invalid YAML must fail loudly at CLI start, not halfway through a crawl.

```yaml
slug: agentic-ai-ch
name: Agentic AI & GenAI enablement providers, Switzerland
geo:
  country: CH
  cantons: null            # null = all

inclusion: >
  Include a company if it offers, as a named service on its own website,
  either (a) development of LLM agents / agentic systems for clients, or
  (b) GenAI enablement workshops or training for organisations.
  Exclude pure product/SaaS companies with no services arm.
  Exclude companies with no Swiss presence.
  Exclude individual freelancers with no registered company.

tiers:
  1: "Agentic AI or GenAI services are the primary offering"
  2: "Broader AI/data consultancy that also ships agents or runs GenAI workshops"
  3: "General digital agency or system integrator with an AI service line"
  4: "Training provider or solo consultant offering GenAI workshops"

enrich_tiers: [1, 2]       # T3/T4 stay as unenriched candidates

facets:
  service_type: [agent_dev, workshops, strategy, rag, automation, staffing, mlops]
  delivery_model: [project, retainer, product, training, staff_aug]
  vertical: [finance, insurance, pharma, public, retail, industrial, legal, none]
  tech: [langchain, langgraph, openai, anthropic, azure_openai, n8n, custom]

sources:
  websearch:
    enabled: true
    queries:
      - "AI Agenten Entwicklung Schweiz"
      - "agentic AI consulting Switzerland"
      - "GenAI Workshop Unternehmen Schweiz"
      - "KI Beratung Schweiz LLM"
      - "AI agency Zurich"
      - 'site:.ch "AI agents" Beratung'
      - 'site:.ch "GenAI Workshop"'
  jobads:
    enabled: true
    keywords: [LangGraph, "AI Engineer", agentic, "LLM Engineer", RAG]
  directories:
    enabled: true
    sites: [goodfirms.co, designrush.com, clutch.co, sortlist.ch]
  lindas:
    enabled: true
    purpose_terms:
      - "Künstliche Intelligenz"
      - "KI-Beratung"
      - "intelligence artificielle"
    note: "High recall, terrible precision. Candidates only, never auto-include."
  seeds:
    enabled: true
    urls: []               # hand-curated; owner fills this

gold_set:                  # for recall measurement — owner fills this
  - domain: example-competitor.ch
    expected_tier: 1
```

**If `gold_set` and `seeds.urls` are still empty when you reach Phase 2:** bootstrap them yourself from web search, mark the file with `# BOOTSTRAPPED BY AGENT — owner must review`, log it under `## Open items for the owner`, and keep going. An empty gold set must not block the build; an unverified one must not be presented as verified.

**Highest-yield discovery channels for this segment, in order** (informs where to invest engineering time):

1. **Job ads** — Swiss agencies staff up for agent work months before they market it. `LangGraph`, `agentic`, `AI Engineer` on jobs.ch surfaces T1 firms earliest.
2. **Event sponsor and speaker lists** — AMLD Lausanne, Swiss Digital Summit, digitalswitzerland members, Zurich AI meetups. GenAI workshop providers sponsor these because it's their lead channel.
3. **Partner directories** — Swiss partner pages of Microsoft, Databricks, AWS, n8n.
4. **Agency directories** — dense, but skew heavily T3.
5. **LINDAS purpose sweep** — good recall on invisible small GmbHs, brutal precision (thousands of hits for generic IT purposes). Candidate pool only.
6. **The owner's own referral graph and lost-pitch "we also considered X" list** — beats all of the above for T1. Seed by hand into `seeds.urls`.

## 10. CLI

```bash
sectorradar init                                  # create data/radar.db, apply schema
sectorradar discover --segment agentic-ai-ch [--source websearch] [--limit 100]
sectorradar resolve  --segment agentic-ai-ch      # dedupe candidates -> companies
sectorradar fetch    --segment agentic-ai-ch [--force]
sectorradar extract  --segment agentic-ai-ch [--model ...] [--only-changed]
sectorradar classify --segment agentic-ai-ch
sectorradar geocode  --segment agentic-ai-ch
sectorradar run      --segment agentic-ai-ch      # all of the above in order
sectorradar stats    --segment agentic-ai-ch      # saturation, gold-set recall, cost
sectorradar snapshot --segment agentic-ai-ch
sectorradar export   --segment agentic-ai-ch --format csv|xlsx|geojson
sectorradar doctor                                # check env vars, DB, tool versions
```

Every command: `--dry-run`, `--verbose`, and structured logging to stderr via `structlog`. `run` must be safely interruptible (SIGINT leaves a consistent DB) and resumable. Exit codes: `0` success, `1` handled failure with a readable message, `2` bad usage. No stack trace reaches the user unless `--verbose`.

## 11. Streamlit app spec

`app/lib/queries.py` holds all SQL. Pages hold no SQL and no business logic.

**Home.py** — segment picker (`st.selectbox`, persisted in `st.query_params`), headline counts by tier and review state, saturation curve chart, gold-set recall, last-run timestamp, "N pending review" call-to-action.

**1_Map.py** — `pydeck` or `folium` via `streamlit-folium`. Markers coloured by tier, sized by headcount estimate. Click → company name, one-liner, top 3 offerings, link to the detail page. Shared sidebar filters. Rows without coordinates listed separately below the map so they aren't silently invisible.

**2_Table.py** — `st.dataframe` with column config. Sidebar filters: tier (multiselect), canton, facet values, review state, headcount range, free-text search hitting `company_fts`. Export button for the current filtered view.

**3_Review.py** — the quality lever. One company at a time from the pending queue: name, domain, one-liner, extracted offerings each with its evidence quote and clickable source URL, proposed tier + rationale. Buttons: accept / reject / retier / needs-info, plus a note field. Writes to `membership`. Keyboard-friendly. Target: 200 companies reviewable in about an hour.

**4_Company.py** — full detail. All `company_field` rows with provenance and extraction timestamps, all offerings, all tags, raw-page links, and a diff against the previous snapshot if one exists.

**Streamlit hygiene:** `@st.cache_data(ttl=60)` on all read queries keyed on DB mtime; open SQLite read-only (`file:...?mode=ro`) everywhere except the review writes; filter state in `st.query_params` so filtered views are shareable links. If `data/radar.db` is absent, every page must render a friendly "run `sectorradar init` first" panel rather than a traceback.

---

# PART IV — PHASED BUILD PLAN

Each phase has a **gate**: a command that must exit 0, and evidence to paste into the ledger. Commit at every gate.

## Phase 0 — repository scaffold and tooling

Nothing in this phase is optional. A repo that will be public and serve as a credibility artefact is judged on this before anyone reads a line of pipeline code.

### 0a. Environment and packaging

- `.python-version` containing `3.11`.
- `pyproject.toml`:
  - `[project]` — name `sectorradar`, version `0.1.0`, `requires-python = ">=3.11"`, `license = "Apache-2.0"`, description from §17, keywords and classifiers, `[project.urls]`, `[project.scripts] sectorradar = "sectorradar.cli:app"`.
  - Runtime deps: `typer`, `pydantic>=2.9`, `pyyaml`, `httpx`, `python-dotenv`, `structlog`, plus what the pipeline needs as you reach it (`rapidfuzz`, `selectolax`/`trafilatura`, the LLM SDK, the search SDK).
  - `[project.optional-dependencies] app` — `streamlit`, `pandas`, `pydeck`. **The app extras must not be a runtime dependency of the pipeline.**
  - `[dependency-groups] dev` — `pytest>=8`, `pytest-cov`, `ruff>=0.9`, `mypy>=1.14`, `pre-commit`, `types-pyyaml`.
  - `[build-system]` — `hatchling`.
  - `[tool.ruff]` — `line-length = 100`, `target-version = "py311"`; `lint.select` covering at least `E,F,I,UP,B,SIM,C4,PTH,RUF,T20,S`; per-file ignores for `S101` in `tests/`, `T20` in `cli.py` and `app/`. No black, no isort configuration.
  - `[tool.mypy]` — `strict = true`, `files = ["src", "tests"]`, `warn_unreachable = true`; `ignore_missing_imports` overrides for untyped third parties only, listed explicitly, never globally.
  - `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `addopts = "--strict-markers --strict-config"`, markers `network` and `llm` registered so live-network and paid tests can be deselected in CI.
  - `[tool.coverage.run]` — `source = ["src"]`, `branch = true`.
- `uv sync`, then commit `uv.lock`.

### 0b. Pre-commit

`.pre-commit-config.yaml` with `default_install_hook_types: [pre-commit, commit-msg]` and:

- `pre-commit/pre-commit-hooks`: `check-added-large-files` (`--maxkb=512`), `check-json`, `check-merge-conflict`, `check-toml`, `check-yaml`, `end-of-file-fixer`, `mixed-line-ending --fix=lf`, `trailing-whitespace`
- `astral-sh/ruff-pre-commit`: `ruff-check --fix`, `ruff-format`
- `astral-sh/uv-pre-commit`: `uv-lock` (keeps the lockfile honest when `pyproject.toml` changes)
- `gitleaks/gitleaks`: `gitleaks`
- Local hooks:
  - **mypy** — `entry: uv run --frozen mypy`, `language: system`, `pass_filenames: false`, `require_serial: true`. The upstream mypy mirror runs in its own env and cannot see project dependencies; it produces phantom import errors. Use the local hook.
  - **no-gcp-service-account-keys** — `language: pygrep`, `entry: '"type"\s*:\s*"service_account"'`, `types: [json]`. Six lines of YAML, not a Python script.
  - **no-agent-coauthors** — `language: pygrep`, `stages: [commit-msg]`, pattern matching `co-authored-by:.*(claude|anthropic|copilot|cursor|gemini|codex)` case-insensitively.
  - **no-collected-data** — `language: fail`, `files: '^data/'`, message pointing at `DATA.md`.

Then: `uv run pre-commit install --install-hooks` and `uv run pre-commit run --all-files`.

**Verify the guardrails rather than assuming them.** Several hooks are skipped on a tree that contains no matching files, and "skipped" is not "verified". For each of these, plant the trigger, confirm the hook fails *and names the file*, then remove it:
- `{"type":"service_account","private_key":"x"}` in a throwaway `.json` → GCP hook fails
- a fake `AKIA`-prefixed AWS key in a throwaway file → gitleaks fails
- `git commit -m "test" -m "Co-Authored-By: Claude <noreply@anthropic.com>"` → commit-msg hook rejects
- `git add -f data/x.txt` → `no-collected-data` fails

Record in the ledger that you did this. An unverified guardrail is worse than none, because everyone assumes it works.

### 0c. Agent and contributor documentation

- **`AGENTS.md`** — the single source of truth. Sections, in this order:
  1. *What this project is* — three sentences, and the `src/` ↔ `app/` boundary rule.
  2. *Setup* — `uv sync`, `uv run pre-commit install`, `cp .env.example .env`.
  3. *The commands you must run before every commit* — the four-command gate, verbatim and copy-pasteable.
  4. *Architecture in one screen* — the pipeline stage list and where each module lives.
  5. *Conventions* — `src/` layout; type hints on every public signature; `logging`/`structlog` never `print` outside entry points; `pathlib` never `os.path`; no bare `except`; no secrets in source; immutable-by-default data handling.
  6. *Testing* — where tests live, the `network` and `llm` markers, the 80% floor, and the rule that `resolve.py` and `extract.py` are test-first.
  7. *Commit and PR conventions* — conventional commits, and **an explicit statement that no AI `Co-Authored-By:` trailer may appear on any commit**.
  8. *Things that will bite you* — a pointer to §16.
  9. *Data and privacy rules* — §14, condensed to five bullets, with a pointer to `DATA.md`.
- **`CLAUDE.md`** and **`GEMINI.md`** — each a pointer, nothing else, so the instructions can never drift out of sync:
  ```markdown
  # Instructions

  See [AGENTS.md](./AGENTS.md). It is the single source of truth for this repository —
  setup, conventions, testing, and commit rules. This file exists only so that tools
  looking for a vendor-specific filename find their way there.
  ```
- **`CONTRIBUTING.md`** — dev setup, the pre-commit gate, conventional commits, how to add a segment (link `docs/adding-a-segment.md`), the crawler-politeness expectation for anyone adding a source.
- **`SECURITY.md`** — how to report a vulnerability (email), what is in scope, the "no secrets in the repo, gitleaks runs in CI" statement, and a 90-day disclosure window.
- **`CODE_OF_CONDUCT.md`** — Contributor Covenant 2.1, contact address filled in.
- **`CHANGELOG.md`** — Keep a Changelog format, `## [Unreleased]` plus `## [0.1.0]` filled in at Phase 7.

### 0d. README

Aimed at a stranger who found the repo, not at the owner. Order:

1. **H1 + one-line subtitle**: *"Who else is in this market, where are they, and what do they actually sell?"*
2. **Badge row** — CI status, licence (Apache-2.0), Python 3.11+, ruff, mypy checked, pre-commit enabled, gitleaks. Use shields.io static badges for the ones with no service behind them; the CI badge points at the workflow.
3. **One paragraph** of what it does, using the §17 wording — "gathers publicly available", "organises", "research". Not "competitors", not "every company".
4. **Screenshot placeholder** for the map view (`docs/img/`), added in Phase 6 once there is something real to capture.
5. **Scope and limitations** — the three bullets from §17, placed *above* installation. This is the first thing a cautious reader looks for.
6. **Architecture diagram** — a Mermaid `flowchart LR` in a fenced ` ```mermaid ` block, rendered natively by GitHub. It must show the two halves and the SQLite file as the only interface between them:

   ```
   discovery sources (websearch, jobads, directories, LINDAS, seeds)
     → discover → resolve → fetch → extract → classify → geocode
     → data/radar.db
     → Streamlit app (Home / Map / Table / Review / Company)
   ```

   with the human review loop drawn as an edge from the app back into the database, and a subgraph boundary making it visually obvious that the app never touches the pipeline. Add a second, smaller `erDiagram` for the core five tables (`company`, `membership`, `company_field`, `offering`, `candidate`) — the provenance model is the interesting part of the design and a diagram sells it faster than the DDL.
7. **Quickstart** — `uv sync`, `cp .env.example .env`, `sectorradar init`, `sectorradar run --segment agentic-ai-ch`, `streamlit run app/Home.py`. Copy-pasteable, in that order, no prose between the blocks.
8. **Defining your own segment** — ten lines of YAML and a link to `docs/adding-a-segment.md`. This is the feature that makes the repo interesting to a stranger; do not bury it.
9. **How it works** — the eight pipeline stages, one line each.
10. **Data sources and attribution** — table of source, what it gives, and its terms; link to `DATA.md`.
11. **Development** — the four-command gate.
12. **Licence** — Apache-2.0, link to `LICENSE` and `NOTICE`.

### 0e. Legal and data files

- `LICENSE` — full Apache-2.0 text, `Copyright 2026 Daniel Vogler`.
- `NOTICE` — project name, copyright holder, two lines.
- `DATA.md` — per §17: never commit collected data; LINDAS/Zefix is "Provide-the-Source", attribution required; directory sites have their own ToS which the *user* is responsible for respecting; the tool stores no personal data about individuals; `data/` is gitignored by design and no sample database ships.

### 0f. CI and repository metadata

- `.github/workflows/ci.yml` — on push and PR to `main`:
  - `astral-sh/setup-uv` with caching, matrix over Python 3.11/3.12/3.13
  - `uv sync --frozen`
  - `uv run ruff check --output-format=github .`
  - `uv run ruff format --check .`
  - `uv run mypy`
  - `uv run pytest -m "not network and not llm" --cov --cov-report=term-missing --cov-fail-under=80`
  - a separate `gitleaks` job with `fetch-depth: 0` scanning full history
  - `concurrency` group cancelling superseded runs; `permissions: contents: read`; all actions pinned to a tag
- `.github/dependabot.yml` — weekly, for `uv` and `github-actions`.
- `.github/CODEOWNERS`, `PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/{bug_report.md,feature_request.md,config.yml}`.
- `.editorconfig`, `.gitattributes` (`* text=auto eol=lf`, mark `uv.lock` `linguist-generated`).
- `.gitignore` — extend the existing file with `data/`, `.env`, `.venv/`, `*-key.json`, `*service-account*.json`, `.streamlit/secrets.toml`.
- `.env.example` — every variable the code reads, with a comment and a safe placeholder, none of them real: LLM provider key, search provider key, `SECTORRADAR_CONTACT` (the crawler User-Agent address), `SECTORRADAR_DB_PATH`, `SECTORRADAR_LOG_LEVEL`.
- `Makefile` — `setup`, `lint`, `fmt`, `typecheck`, `test`, `run`, `app`, `clean`. One entry point people will actually use. Two targets carry weight:
  - **`make check`** — the repo-only health check, with no dependency on `data/radar.db` or on any API key: ruff check, ruff format --check, mypy, `pytest -m "not network and not llm" --cov-fail-under=80`, `pre-commit run --all-files`, `gitleaks detect --no-git --redact`, `[ -z "$(git ls-files data/)" ]`, and the empty-`co-authored-by` assertion. **Must pass from a bare `git clone` after `uv sync --frozen`** — that property is what makes CI meaningful, and Phase 7 tests it explicitly.
  - **`make verify`** — the completion condition from §0.4, and the single most important target in the file. It runs `check` first, then every data-dependent phase gate from Part IV against the local `data/radar.db`: row counts, tier coverage, gold-set recall, the second-segment test. Write it in Phase 0 with the later gates present but guarded — a gate whose phase is not yet reached prints `SKIP: phase N not reached` and continues, and **must flip to a hard check the moment that phase's code lands**. By Phase 7 nothing is skipped, and `make verify` exits 0 only if the entire build is genuinely done.

    Two rules about this target. It is append-only in spirit: checks get added, never removed or softened to go green — the evaluator reads the Makefile, and a `verify` that passes because it stopped checking is the one outcome worse than an honest failure. And a `SKIP` is not a pass: if any `SKIP` line is printed, the completion condition is not met, so make the target say so in its final line.

### 0g. Skeleton package

`src/sectorradar/__init__.py` with `__version__`, `src/sectorradar/py.typed`, a `cli.py` exposing `sectorradar --help` and `sectorradar doctor`, and `tests/test_smoke.py` with one real assertion so `pytest` is green from minute one.

**Gate 0**

```bash
uv sync --frozen \
  && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy \
  && uv run pytest \
  && uv run pre-commit run --all-files \
  && uv run sectorradar --help \
  && git log --format='%B' | grep -ci 'co-authored-by' | grep -q '^0$' \
  && echo GATE_0_PASS
```

Plus, pasted into the ledger: the four planted-guardrail checks from §0b, each showing the hook failing.

## Phase 1 — schema, config, CLI skeleton

`db.py` (full DDL from §6, indexes, FTS table, `schema_version` migrations), `models.py` (§8), `config.py` (`.env` + segment YAML → validated pydantic `Settings` and `Segment`), `logging.py` (structlog, configured once), `cli.py` (`init`, `doctor`, all pipeline subcommands present as stubs that exit 2 with "not implemented" — the surface is fixed early so nothing downstream has to guess it).

Tests: `test_db.py` (init is idempotent; every table and index exists; re-running migrations is a no-op), `test_config.py` (a malformed segment YAML raises a readable error naming the field), `test_architecture.py` (the AST import-boundary check from §5).

Empty Streamlit `Home.py` that opens the DB read-only and prints zero counts, and renders the friendly panel when the DB is absent.

**Gate 1**

```bash
rm -f data/radar.db && uv run sectorradar init && uv run sectorradar doctor \
  && uv run pytest -q && echo GATE_1_PASS
```
plus `streamlit run app/Home.py` showing "0 companies" — confirm by screenshot or by curling the health endpoint, and note it in the ledger.

## Phase 2 — manual spine

Seeds → resolve → geocode → visible on a map. **This is the demo that proves the idea; get here fast.**

- `sources/seeds.py` and the `SOURCES` registry.
- `resolve.py` — **write `tests/test_resolve.py` first**, one test per trap in §7. Then implement until green.
- `geocode.py` with the disk cache.
- `app/lib/queries.py`, `app/lib/filters.py`, `1_Map.py`, `2_Table.py`.
- Populate `segments/agentic-ai-ch.yaml` `seeds.urls` with ≥ 20 real Swiss firms (bootstrap per §9 if the owner has not filled it).

**Gate 2**

```bash
uv run sectorradar discover --segment agentic-ai-ch --source seeds \
  && uv run sectorradar resolve --segment agentic-ai-ch \
  && uv run sectorradar geocode --segment agentic-ai-ch \
  && uv run pytest -q \
  && sqlite3 data/radar.db "select count(*) from company where lat is not null" \
  && echo GATE_2_PASS
```
Requires ≥ 20 companies with coordinates, rendering on the map with working filters.

## Phase 3 — enrichment

`fetch.py`, `extract.py`, `classify.py`, prompts under `src/sectorradar/prompts/`, and `3_Review.py`.

The evidence-substring check in `extract.py` is **not deferrable** — implement it in this phase, with a test that feeds the model's output a fabricated quote and asserts the claim is dropped.

Run the full enrichment over the Phase 2 seeds and review all of them through the UI.

**Gate 3**

```bash
uv run sectorradar fetch --segment agentic-ai-ch \
  && uv run sectorradar extract --segment agentic-ai-ch --only-changed \
  && uv run sectorradar classify --segment agentic-ai-ch \
  && uv run pytest -q \
  && sqlite3 data/radar.db "select count(*) from offering where evidence_quote != ''" \
  && echo GATE_3_PASS
```
Requires: every seeded company has ≥ 1 offering with a clickable evidence quote, a non-null `tier` and `tier_rationale`, and the Review page can accept/reject and persist.

## Phase 4 — discovery

`websearch.py` first (highest yield per line of code), then `jobads.py`, then `directories.py`. Saturation tracking per §7. `stats.py` with gold-set recall.

Expect directory sites to bot-block; when one does, record it in the ledger, fall back to using its results as seeds only, and move on. Do not build evasion.

**Gate 4**

```bash
uv run sectorradar run --segment agentic-ai-ch \
  && uv run sectorradar stats --segment agentic-ai-ch \
  && echo GATE_4_PASS
```
Requires: gold-set recall ≥ 80%, ≥ 100 candidates, saturation curve rendering on Home.

## Phase 5 — long tail

`sources/lindas.py` (SPARQL purpose sweep, candidates only), snapshots, `export.py` (csv/xlsx/geojson), and a **second segment** — `segments/genai-training-ch.yaml`, the T4 workshop-provider set — created as one YAML file with **zero code changes**. If it needs a code change, the abstraction is wrong; fix the abstraction, not the YAML.

**Gate 5**

```bash
uv run sectorradar run --segment genai-training-ch \
  && uv run sectorradar snapshot --segment agentic-ai-ch \
  && uv run sectorradar export --segment agentic-ai-ch --format geojson \
  && git diff --stat HEAD~1 -- src/ | grep -q . && echo "CODE CHANGED — abstraction leaked" || echo GATE_5_PASS
```

## Phase 6 — hardening

- Coverage to ≥ 80% on `src/`, with the gaps that remain being genuinely untestable I/O, not skipped logic.
- `mypy --strict` clean with no `# type: ignore` that lacks an error code and a one-line reason.
- `docs/architecture.md`, `docs/adding-a-segment.md`, `docs/operations.md` written.
- README screenshot captured into `docs/img/` and wired up.
- Every module docstring present; every public signature typed.
- `sectorradar doctor` reports env, DB, versions and row counts usefully.

**Gate 6**

```bash
uv run pytest --cov --cov-report=term-missing --cov-fail-under=80 \
  && uv run mypy \
  && uv run pre-commit run --all-files \
  && echo GATE_6_PASS
```

## Phase 7 — release readiness

- `CHANGELOG.md` `## [0.1.0]` written from the commit history.
- Version bumped and consistent between `pyproject.toml` and `__init__.__version__`.
- `git tag v0.1.0` (local only — do not push).
- Final sweep: `git log --format='%B' | grep -i 'co-authored-by'` empty; `gitleaks detect --no-git` clean; `git ls-files data/` empty.
- `notes/PROGRESS.md` complete, every gate ticked with its output.
- Write `## Open items for the owner` into the ledger: the gold set to verify, the search-provider choice, the T4-segment question, and anything you bootstrapped.

**Gate 7**

```bash
make verify && echo GATE_7_PASS
```

By this phase `make verify` skips nothing, so this one command is the whole build's acceptance test. It runs **in the working repository**, because its data-dependent assertions read `data/radar.db`, which is gitignored and by design does not survive a clone.

Separately prove that the *code* carries no untracked local dependency:

```bash
git clone . /tmp/sr-verify && cd /tmp/sr-verify && uv sync --frozen && make check
```

`make check` is the repo-only half — lint, format, mypy, tests, gitleaks, the two git assertions — and must pass from a bare clone. If it fails there but passes at home, something real is sitting untracked in your working directory; find it before declaring done.

Then, and only then, the completion condition in §0.4 is satisfied.

---

# PART V — CONTEXT THE IMPLEMENTER WILL WANT

## 13. Cost and scale

At 200 enriched companies × ~5 pages each:

- Search API: ~200 queries → a few USD (Exa/Brave/Tavily all in this range)
- Fetching: free, ~1–2 hours wall-clock at polite rates
- LLM extraction + classification: ~1000 page-extractions on a small model → single-digit USD
- Geocoding: free
- Storage: `data/radar.db` well under 50 MB; `data/raw/` a few hundred MB

Re-runs are far cheaper because of `content_sha` skipping. Budget roughly USD 5–15 for a full cold build of a segment; the hard ceiling for this whole build is USD 25 (§0.2).

## 14. Legal and ethical constraints

These are constraints on the code, not aspirations. Encode them.

- Company data is not personal data — the segment is fine under revDSG/GDPR. **Do not harvest employee names, emails, or LinkedIn profiles into the DB.** If a team page is fetched, extract headcount only, never individuals. The extraction prompt must say so explicitly, and a test should assert that an obviously personal field is refused.
- Respect `robots.txt`. Rate limit. Identify the crawler with a contact address in the User-Agent, read from `SECTORRADAR_CONTACT`; **refuse to crawl if that variable is unset** rather than sending a generic UA.
- Directory sites (Clutch, GoodFirms) have ToS restricting scraping. Prefer their search results as *seeds* for finding company domains, then crawl the company's own site — don't mirror directory content.
- Store `source_url` + `fetched_at` on every claim. This is both the trust mechanism and the compliance record.
- The database is competitive intelligence about businesses from their own public marketing. Keep it that way.
- If a site returns 403 or a bot-block page, back off and record it. Never work around a block.

## 15. Open questions for the owner — do not block on these

Note them in the ledger, take the recommended default, continue.

1. **Gold set** — needs 25–30 real competitor domains with expected tiers. *Default: bootstrap from search, mark as unverified.*
2. **Search provider** — Exa (best for "find companies like this", neural), Brave (cheapest breadth), Tavily (agent-shaped API). *Default: whichever key exists in `.env`; if several, Exa for discovery and Brave for cheap bulk.*
3. **LLM provider/model** for extraction. *Default: match whatever key exists in `.env`; prefer the cheapest capable model.*
4. **Does the T4 workshop-provider set belong in the same segment or its own?** *Default: its own segment file, shared schema — this doubles as the Phase 5 abstraction test.*
5. **Refresh cadence** once v1 works. *Default: none; v1 is manual by design.*

## 16. Things that will go wrong

- **Resolve will be the time sink,** not discovery or extraction. Budget for it and write the tests first.
- **Directory sites will bot-block you.** Expect to hand-seed more than planned.
- **The T3 pool will balloon and swamp the UI.** Default every view to `tier <= 2` and make T3 opt-in.
- **The LLM will confidently invent offerings** from generic marketing copy. The substring check on `evidence_quote` is the defence — implement it in Phase 3, not later.
- **Streamlit will re-run the whole script on every widget interaction.** If anything expensive leaks into the app layer, it becomes unusable at 200 rows. Hold the `src/`↔`app/` boundary; `test_architecture.py` is what keeps you honest.
- **`mypy --strict` on a pipeline full of `dict[str, Any]` from APIs** will be painful in Phase 4 if you leave it until Phase 6. Type the boundary at the moment you write it: parse every external response into a pydantic model immediately, and let nothing untyped past the module edge.

## 17. Publication: description, topics, licence

The repo will be **public**. Wording is deliberately research-framed rather than competition-framed — the same tool described as "find all your competitors" reads as a surveillance product to a stranger, and that's the wrong first impression for something that will also serve as a credibility artefact for the owner's practice.

### GitHub description

> Turn a market segment into a structured, browsable dataset. Gathers publicly available company information, organises it with source citations, and serves it as a filterable table and map. Segments are defined in YAML, so you can point it at any industry or country.

Alternates if a shorter form is needed:

- *Research and visualise the companies working in a given industry and region. Public sources, cited profiles, filterable table and map. Configure a new segment with a YAML file.*
- *A small research tool for mapping industries. Combines public web sources and open company registry data into a structured dataset you can filter, sort and view on a map.*

Deliberately avoided: "every company", "all companies", "competitors", "competitive intelligence", "discovers", "extracts". These make the tool sound exhaustive and targeted. Use "gathers publicly available", "organises", "research".

### Topics

```
market-research  company-data  open-data  data-pipeline  streamlit
sqlite  llm  python  switzerland  zefix  geospatial  research-tools
```

### README "Scope and limitations"

Place near the top, above installation. Three sentences, all true of the design as specified:

- Reads only publicly accessible web pages and open company registries.
- Respects `robots.txt` and rate-limits requests, identifying itself with a contact address.
- Stores no personal data about individuals — team pages contribute headcount estimates only, never names or contact details.

Suggested README subtitle, which carries the idea faster than any feature list: *"Who else is in this market, where are they, and what do they actually sell?"*

### Licence: Apache-2.0

- **Patent grant.** The project centres on LLM extraction, entity resolution and evidence-verification methods, in a space the owner works in commercially. Apache-2.0's express patent licence and retaliation clause protect both maintainer and users from a contributor later asserting patents over upstreamed work. MIT is silent on patents.
- **Enterprise legibility.** Apache-2.0 clears corporate legal review without discussion. AGPL would be actively counterproductive — it's reflexively blocked at exactly the Swiss consultancies and corporate teams whose goodwill the repo is meant to earn.
- **NOTICE file.** A standard, durable place for attribution that survives forks.

`pyproject.toml` carries `license = "Apache-2.0"` and the matching classifier.

### DATA.md — data licensing, separate from the code licence

The code licence says nothing about what the tool collects. This matters more than MIT-vs-Apache. Three requirements:

1. **Never commit collected data.** `data/` stays gitignored; ship no sample database. A public repo containing a scraped company dataset takes on redistribution obligations from sources whose terms vary and haven't been checked.
2. **Attribute registry data.** The LINDAS Zefix dataset carries a "Provide-the-Source" rights designation — free to use, attribution required. Anything derived from it must name the source.
3. **State source terms rather than sublicensing them.** Cover: which sources the tool reads; that registry data is open with attribution; that directory sites (Clutch, GoodFirms, DesignRush, Sortlist) have their own terms of service which the *user* is responsible for respecting; and that the tool stores no personal data about individuals.

> Note: the licensing guidance above is general information about common practice, not legal advice. If the repo becomes entangled with client work, or commercial options on a derivative need to stay open, that warrants a short conversation with a lawyer before publishing.
