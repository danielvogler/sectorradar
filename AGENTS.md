# AGENTS.md

The single source of truth for anyone working in this repository, human or
agent. `CLAUDE.md` and `GEMINI.md` are pointers here and hold no content of
their own.

## 1. What this project is

`sectorradar` turns a market segment into a structured, browsable dataset. It
gathers publicly available company information, organises it with a source
citation attached to every claim, and serves it as a filterable table and map.
Segments are defined in YAML, so pointing it at a new industry or country is a
configuration change rather than a code change.

The repository has two halves, and **the boundary between them is the most
important rule in this file**:

- `src/sectorradar/` is the pipeline. It crawls, calls LLMs, and writes SQLite.
- `app/` is a Streamlit front end. It only ever *reads* SQLite.

`data/radar.db` is the sole interface between them. `app/` must never import
from `src/sectorradar/`, and nothing under `src/` may import `streamlit`.
Streamlit re-runs the entire script on every widget interaction; if anything
expensive leaks across that line the app becomes unusable at a few hundred rows.
`tests/test_architecture.py` walks the AST of every file and fails the build if
either direction of the boundary is crossed.

## 2. Setup

```bash
uv sync
uv run pre-commit install --install-hooks
cp .env.example .env   # then fill it in
```

`SECTORRADAR_CONTACT` is not optional. The crawler refuses to run without it.

## 3. The commands you must run before every commit

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

## 4. Architecture in one screen

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
| review | `app/pages/3_Review.py` | Human accept/reject — a first-class stage, not an afterthought |
| snapshot | `db.py` | Freezes the accepted set so change over time is reconstructable |

Supporting modules: `config.py` (env + segment YAML → validated models),
`db.py` (DDL, migrations, upserts), `models.py` (pydantic contracts),
`logging.py` (structlog, configured once from `cli.py`), `stats.py`
(saturation, gold-set recall, cost), `export.py`, `cli.py`.

## 5. Conventions

- `src/` layout. The package is `sectorradar`; imports are absolute.
- Type hints on every public signature. `mypy --strict` covers `src/` and
  `tests/`. A `# type: ignore` must carry an error code and a one-line reason.
- `structlog`, never `print`, outside `cli.py` and `app/`.
- `pathlib`, never `os.path`.
- No bare `except`. Catch the exception you mean.
- No secrets in source. Everything comes from the environment via `config.py`.
- Immutable by default: build new objects rather than mutating in place.
- Parse every external response into a pydantic model at the module edge. Do not
  let `dict[str, Any]` from an API travel further into the codebase — deferring
  this is what makes `mypy --strict` painful later.
- Files stay focused: roughly 200–400 lines, 800 as a hard ceiling.

## 6. Testing

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

## 7. Commit and PR conventions

Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`,
`perf:`, `ci:`. Subject ≤ 72 characters, imperative mood. The body explains
*why*, not *what*.

**No `Co-Authored-By:` trailer naming Claude, Anthropic, Gemini, Codex, Copilot,
Cursor or any other AI agent may appear on any commit in this repository.**
Three layers enforce it: the Claude Code setting `includeCoAuthoredBy: false`, a
`commit-msg` pre-commit hook, and an assertion in both `make check` and CI that
`git log --format='%B' | grep -i 'co-authored-by'` is empty.

## 8. Things that will bite you

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

## 9. Data and privacy rules

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
