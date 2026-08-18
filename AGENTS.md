# AGENTS.md

The single source of truth for anyone working in this repository, human or
agent. `CLAUDE.md` and `GEMINI.md` are pointers here and hold no content of
their own.

**There are two jobs in this file.** Helping somebody *use* the tool is §A.
Changing the code is §B. Read the one you are here for; they need almost
nothing from each other.

---

# §A — Helping somebody map a market

Assume they have cloned the repository and know nothing else. Your job is to
get them from that to a map of their market, and to be honest with them about
what it does and does not know. Work through this in order and do not skip
ahead: the most common way this goes wrong is a segment file written before
anybody asked what the market actually is.

## A1. Find out what they want, before touching anything

Ask, and wait for real answers:

- **What market?** "Digital marketing agencies in Switzerland", not "marketing".
- **What will they do with the list?** The three answers pull the boundary in
  different directions, so ask before writing anything:
  *finding a supplier* wants firms that can show delivered work, at any size;
  *positioning their own offering* wants direct competitors and excludes firms
  too small to matter; *finding where a market is thin* wants the widest net
  you can defend.
- **Do they have companies in this market themselves?** If so, those go in the
  local overlay and are held out of every baseline. A comparison you are inside
  tells you that you are average.

If they cannot name five companies obviously in the market, the segment is not
yet a segment. Help them get there before writing YAML.

## A2. Set it up

```bash
make setup                 # venv, dependencies, git hooks, .env
```

Then fill in `.env` with them:

- `SECTORRADAR_CONTACT` — a **real** address. The crawler refuses to start
  without one, because every request it makes carries it. This is not
  ceremony: it is what makes the crawling defensible.
- `SECTORRADAR_LLM_PROVIDER` — `vertex`, `anthropic` or `openai`.
- `SECTORRADAR_SEARCH_PROVIDER` — `vertex_grounding` or `anthropic` need no
  extra key; `exa`, `brave` and `tavily` each need their own.

Install only the extra they use: `uv sync --extra anthropic`. For Vertex, they
run `gcloud auth application-default login` — **never** create or download a
service-account key for this.

Then `uv run sectorradar init` and `uv run sectorradar doctor`. Doctor tells you
what is missing before a crawl does.

## A3. Write the segment file with them

**Read [segments/AGENTS.md](./segments/AGENTS.md) now.** It is the substance of
this step: the six questions, how a near-miss becomes an exclusion rule, how to
size a vocabulary, and how to write evidence words in the languages the market
sells in. Do not improvise it — a segment file that runs cleanly and produces a
useless dataset costs a day to discover.

The two mistakes that matter most, because both are silent:

- **A vocabulary without evidence words.** A tag survives only if its words
  appear on the page. `seo` never matches *Suchmaschinenoptimierung*, so the
  table comes out empty and the market looks thin.
- **Queries in one language.** In a multilingual market this quietly misses
  whole regions, and the result still looks like a full dataset.

Anything personal — their own companies, their seed list, the firms they have
already know belong — goes in `segments/<slug>.local.yaml`, which is gitignored and
merged over the committed file. Lists replace wholesale, so a block there must
be complete.

## A4. Run it, cheaply first

```bash
uv run sectorradar discover --segment <slug> --dry-run   # look before paying
uv run sectorradar deepen   --segment <slug>             # search until it stops finding new firms
uv run sectorradar run      --segment <slug>             # the rest of the pipeline
```

Discovery is the cheap part and the part most likely to be wrong. Look at what
it found before paying for extraction on it.

**Use `deepen`, and do not try to do its job yourself.** It repeats discovery,
writes fresh queries between rounds aimed at what the previous rounds *missed*,
and stops once a round turns up almost nothing new — or at a round or spend
limit, reporting which. On its
first real run it reached Glarus, Uri, Appenzell, Jura and Ticino, none of
which the hand-written queries had touched.

This is deliberately a loop in the tool rather than an instruction to you.
Whether a market has been searched enough is not a judgement any agent should
be making from vibes, and different agents give up at different points. Read
what it prints: if it says it stopped at the round cap while still finding new
companies, **it has not finished**, and running it again is the right call.

## A5. Read the audit, then fix the config rather than the code

Every run ends with a coverage report. **Read it with them and act on it.** It
exists because every gap this tool has ever had was found by a person noticing
something looked off — one company in Basel, ninety-nine without an address —
and nobody running it for the first time knows what wrong looks like.

Almost every finding is fixed in the segment YAML, not in Python:

| Finding | Usually means |
|---|---|
| declared values never applied | evidence words do not match the market's language |
| a populous region nearly empty | no city-level query reached it |
| known companies not found | queries do not match how a buyer would search |
| known companies classified out | the inclusion rule is too tight |
| addresses never geocoded | just run `sectorradar geocode` |

`sectorradar classify` re-runs without re-crawling, so fixing a boundary or a
vocabulary is cheap. Re-crawling is not.

## A6. Show them the result

```bash
make serve                 # export, build, open at localhost:8080
```

Tell them plainly what the numbers do and do not mean:

- Nothing here was written by the tool. Opening a company shows the quote from
  their own website behind every line, and a link to the page it sits on.
- A company with nothing published scores zero **and** is flagged unknown. That
  is not a judgement about the company, it is the absence of evidence.
- Search visibility is measured from markup only. Not backlinks, not rankings.
- A rerun will not return the same companies. Search moves, sites change,
  models drift. The gold set is the check that matters.

## A7. If they want to share it

`make web` produces a folder that opens with no server — hand that over and
nothing else is needed.

For a team, publish to object storage instead:

```bash
make bucket                        # uniform access, versioning, public access blocked
make bucket-grant EMAIL=them@example.com
make publish                       # dry run: prints what it would send
make publish EXECUTE=1
```

**Never make the bucket public**, and confirm before creating or destroying
anything in their cloud account — `make bucket-destroy` deletes data, and
`make publish` is outward-facing. Publishing needs the optional extra:
`uv sync --extra gcp`.

What they send a colleague is two strings — the project and the bucket — and
nothing else. Not the segment file: see §A8 for why that matters.

## A8. Setting somebody up to *read* a published dataset

A different person from §A1: they are not mapping anything, they want to look
at a result somebody else produced.

**Ask which of the two they want, because most people want the first.**

**Just to look at it.** The built page is published alongside the data, and it
is a folder of static files. Two commands, and they need neither this
repository nor Python nor Node:

```bash
gcloud auth login
gcloud storage cp -r "gs://<bucket>/site/<segment>/*" sectorradar --project=<project>
# then open sectorradar/index.html
```

**To work with the data.** Everything below — worth the setup only if they
intend to re-filter, re-export, or eventually crawl something themselves. It
needs no database, no API keys, no crawl, and **no segment definition**.

```bash
git clone <repo> && cd sectorradar
make setup
```

Two lines in `.env`, which is all they receive:

```
SECTORRADAR_GCP_PROJECT=<project>
SECTORRADAR_GCS_BUCKET=<bucket>
```

Then:

```bash
gcloud auth application-default login   # the account that was granted access
uv sync --extra gcp
make web-install                        # once, for the page build
make app                                # pulls, builds, serves on :8080
```

`make app` asks the bucket what it holds: one dataset and it takes that one,
several and it lists them so they can pick with `make app SEGMENT=<slug>`.

**They never need the segment file, and that is deliberate.** The market most
worth sharing a result for is often one kept out of the repository entirely, so
requiring the reader to hold a config they were never given would make the
whole arrangement circular. The published document carries the market's own
name, inclusion rule, tiers and vocabulary — which is what lets them read the
"How this was assembled" section and interpret any figure on the page.

Things worth telling them, because the page does not shout them:

- **Clicking a company** shows the quote from its own website behind every
  line, and a link to the page it sits on.
- **The excluded candidates are still there**, each with a written reason. The
  tier filter has an option for them.
- **The score ring is visible traction** — a floor on what a firm can
  demonstrate publicly, not a measure of how well it is doing. A dash means
  nothing was published, which is not the same as doing badly.

---

# §B — Changing the code

## B1. What this project is

`sectorradar` turns a market segment into a structured, browsable dataset. It
gathers publicly available company information, organises it with a source
citation attached to every claim, and serves it as a filterable table and map.
Segments are defined in YAML, so pointing it at a new industry or country is a
configuration change rather than a code change.

The repository has two halves, and **the boundary between them is the most
important rule in this file**:

- `src/sectorradar/` is the pipeline. It crawls, calls LLMs, and writes SQLite.
- `web/` is an Astro front end. It only ever reads **the exported JSON**.

`sectorradar export --format web` writes one document; the front end imports
it. That is the whole interface. The front end must never reach for
`data/radar.db` or issue SQL, and nothing under `src/` may import a view
package. Both directions fail quietly, which is why they are enforced rather
than agreed: a page that reads SQLite still works on the machine that has one
and only breaks for the colleague you sent the folder to.
`tests/test_architecture.py` walks the AST and the page sources, and fails the
build if either direction is crossed.

### The module map

| Module | Job |
|---|---|
| `config.py` | Env + segment YAML → validated models. Merges a gitignored `<slug>.local.yaml` overlay |
| `discover.py`, `sources/` | Find candidates: web search, job ads, directories, the Swiss registry, seeds |
| `resolve.py` | Normalise and dedupe candidates into companies |
| `fetch.py` | Polite concurrent crawler, caches raw HTML |
| `extract.py` | LLM → profile. Every claim quote-checked against the page |
| `classify.py` | Tier, rationale, and facet tags grounded in site text |
| `geocode.py` | Address → coordinates, cache-first, refuses implausible matches |
| `seo.py` | Search visibility from stored markup. Deterministic, no LLM |
| `traction.py` | Evidence → a 0-100 score that reports silence as unknown |
| `analytics.py` | Segment aggregates. Excludes own companies *and* rejected candidates |
| `industries.py`, `swiss.py` | Closed vocabularies for sectors, cantons, cities, languages |
| `export.py` | The one JSON document the front end reads |
| `publish.py` | Publish that document to GCS, and pull it back |

## B2. Setup

```bash
uv sync
uv run pre-commit install --install-hooks
cp .env.example .env   # then fill it in
```

`SECTORRADAR_CONTACT` is not optional. The crawler refuses to run without it.

Providers are configurable and none is privileged. `SECTORRADAR_LLM_PROVIDER`
takes `vertex`, `anthropic` or `openai`; `SECTORRADAR_SEARCH_PROVIDER` takes
`vertex_grounding`, `anthropic`, `exa`, `brave` or `tavily`. All three model
providers do structured output natively, so the pipeline gets the same
guarantee whichever you pick. Install only the extra you use:
`uv sync --extra anthropic`.

## B3. The commands you must run before every commit

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Pre-commit enforces most of this automatically. **Never use `git commit
--no-verify`.** If a hook is wrong, fix the hook in a commit of its own.

Two broader targets sit above that gate:

- `make check` — repo-only health: the four commands above plus coverage,
  `pre-commit run --all-files`, `gitleaks`, and the two git assertions. It has
  no dependency on a database or an API key and must pass from a bare clone.
- `make verify` — `make check` plus every data-dependent gate, run against the
  local `data/radar.db`. This is the project's acceptance test.

## B4. Architecture in one screen

```text
discover → resolve → fetch → extract → classify → geocode → review → snapshot
```

Every stage is independently re-runnable and idempotent. Each reads and writes
the database; none carries state in memory across a run.

| Stage | Module | Does |
|---|---|---|
| discover | `discover.py`, `sources/*.py` | Runs each enabled source, writes `candidate` rows |
| resolve | `resolve.py` | Normalises and dedupes candidates into canonical `company` rows |
| fetch | `fetch.py` | Polite crawler; caches raw HTML under `data/raw/` |
| extract | `extract.py` | LLM → `CompanyProfile`, every claim carrying evidence |
| classify | `classify.py` | Tier, rationale, relevance and facet tags |
| geocode | `geocode.py` | Address → coordinates, cache-first |
| snapshot | `db.py` | Freezes the accepted set so change over time is reconstructable |

`review_state` still exists on `membership` and `classify` still refuses to
overwrite a decision that is not `pending`, so a reviewed tier is safe. There
is no review *interface*: ranking by tier and by evidence turned out to answer
the question a per-company accept/reject was standing in for, and a queue of
280 companies nobody was going to work through is worse than no queue.

Supporting modules: `config.py` (env + segment YAML → validated models),
`db.py` (DDL, migrations, upserts), `models.py` (pydantic contracts),
`logging.py` (structlog, configured once from `cli.py`), `stats.py`
(saturation, gold-set recall, cost), `analytics.py` (segment aggregates),
`traction.py` (evidence → a 0-100 score with its reasoning), `swiss.py`
(canton and city normalisation), `export.py`, `cli.py`.

## B5. Conventions

- `src/` layout. The package is `sectorradar`; imports are absolute.
- Type hints on every public signature. `mypy --strict` covers `src/` and
  `tests/`. A `# type: ignore` must carry an error code and a one-line reason.
- `structlog`, never `print`, outside `cli.py` and `scripts/`.
- `pathlib`, never `os.path`.
- No bare `except`. Catch the exception you mean.
- No secrets in source. Everything comes from the environment via `config.py`.
- Immutable by default: build new objects rather than mutating in place.
- Parse every external response into a pydantic model at the module edge. Do not
  let `dict[str, Any]` from an API travel further into the codebase — deferring
  this is what makes `mypy --strict` painful later.
- Files stay focused: roughly 200–400 lines, 800 as a hard ceiling.

## B6. Testing

Tests live in `tests/`, mirroring module names. Two markers are registered:

- `network` — performs live network I/O
- `llm` — spends money on an LLM provider

CI runs `-m "not network and not llm"`, so neither may be required for the suite
to be meaningful. Coverage floor is **80%** on `src/`.

**`resolve.py` and `extract.py` are test-first.** Those two modules are where
correctness is not self-evident: entity resolution has to survive Swiss
legal-suffix and umlaut variants, and extraction has to survive a model that
will confidently invent services from generic marketing copy. Write the failing
test, then the implementation. Elsewhere, tests alongside is fine.

## B7. Commit and PR conventions

Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`,
`perf:`, `ci:`. Subject ≤ 72 characters, imperative mood. The body explains
*why*, not *what*.

**No `Co-Authored-By:` trailer naming Claude, Anthropic, Gemini, Codex, Copilot,
Cursor or any other AI agent may appear on any commit in this repository.**
Three layers enforce it: the Claude Code setting `includeCoAuthoredBy: false`, a
`commit-msg` pre-commit hook, and an assertion in both `make check` and CI that
`git log --format='%B' | grep -i 'co-authored-by'` is empty.

## B8. Things that will bite you

> The full list — twenty decisions that look wrong until you know why — is in
> [docs/architecture.md](./docs/architecture.md). Read it before removing
> anything that looks redundant. These four bite soonest.

- **Resolve is the time sink**, not discovery or extraction. Swiss traps: the
  same firm as "X Consulting" / "X Consulting GmbH" / "X Consulting Sàrl";
  umlaut variants (Zürich / Zurich / Zuerich); DE/FR/IT names for one entity;
  holding and operating companies sharing a website.
- **Directory sites bot-block.** When one does, record it and fall back to using
  its results as seeds. Never build evasion.
- **The tier-3 pool balloons and swamps the UI.** Default every view to
  `tier <= 2` and make tier 3 opt-in.
- **The LLM invents offerings.** The evidence-substring check in `extract.py` is
  the defence, and it is not optional: every quote must be a genuine substring
  of the fetched page or the claim is dropped.

## B9. Data and privacy rules

Constraints on the code, not aspirations. Full detail in [DATA.md](./DATA.md).

- **Never commit collected data.** `data/` is gitignored by design; a
  `no-collected-data` pre-commit hook and a CI assertion both enforce it.
- **No personal data about individuals.** Team pages contribute headcount
  estimates only — never names, emails or profile links. The extraction prompt
  says so explicitly and a test asserts it.
- **Respect `robots.txt`**, rate-limit to 1–2 requests/second per host, and
  identify the crawler with the contact address from `SECTORRADAR_CONTACT`.
- **Never work around a block.** A 403 or bot-block page means back off and
  record it.
- **Store `source_url` and `fetched_at` on every claim.** This is simultaneously
  the trust mechanism and the compliance record.
