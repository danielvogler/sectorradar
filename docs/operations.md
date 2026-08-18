# Operations

Notes for actually running `sectorradar` against a segment: what it costs,
how fast it can go without getting your crawler blocked, what re-running
does and doesn't repeat, and what to do when something breaks.

## Cost

Two of the six pipeline stages spend money: `extract` and `classify`, both
LLM calls. `discover`, `resolve`, `fetch` and `geocode` are free (search via
`vertex_grounding` is the one exception, discussed below).

The pricing lives in `PRICES` in `src/sectorradar/llm.py`, USD per million
tokens as `(input, output)`:

| Model | Input | Output |
|---|---|---|
| `gemini-2.5-flash-lite` (default) | $0.10 | $0.40 |
| `gemini-2.5-flash` | $0.30 | $2.50 |
| `gemini-2.5-pro` | $1.25 | $10.00 |

An unlisted model falls back to the flash-lite price rather than erroring, so
a typo'd `SECTORRADAR_LLM_MODEL` under-reports cost instead of crashing —
worth knowing if a number looks suspiciously cheap.

### Per-run cost model

Roughly one LLM call per company for `extract` (building the structured
profile from its fetched pages) plus one more per company for `classify`
(assigning tier, rationale and facets). Both calls are cheap per company
individually because `gemini-2.5-flash-lite` is the default and each prompt
is bounded by a handful of fetched pages, not a large corpus.

A worked estimate for ~200 companies at the default model: if `extract`
averages roughly 3,000 input tokens (a few crawled pages of company copy) and
300 output tokens (the structured profile) per company, and `classify`
averages roughly 1,500 input tokens (profile + inclusion prompt) and 150
output tokens (tier + rationale + facets) per company:

```
extract:  200 × (3000 × 0.10 + 300 × 0.40) / 1,000,000 ≈ $0.08
classify: 200 × (1500 × 0.10 + 150 × 0.40) / 1,000,000 ≈ $0.04
total:                                                  ≈ $0.12
```

Both `extract` and `classify` print `cost   USD 0.0000` from their actual
token usage after every run — treat the estimate above as a sanity check
before a run, not a substitute for the real number after one. `stats` does
not currently total historical cost across runs; the number printed at the
end of `extract`, `classify`, and the combined figure at the end of `run` are
the cost record.

### Search

`SECTORRADAR_SEARCH_PROVIDER=vertex_grounding` (the default) bills to the
configured GCP project rather than a standalone API key — it reuses the same
Vertex AI credentials as extraction, so a `websearch` run has no separate
line item to budget beyond your GCP bill. `exa`, `brave` and `tavily` are
metered against their own API keys instead, each with its own pricing this
repo doesn't set.

### Free stages

`geocode` and `fetch` cost nothing beyond wall-clock time and politeness
budget — swisstopo and Nominatim are free public services, and crawling a
company's own site spends no money, only rate-limit patience.

## Rate limits and politeness

`fetch.py` enforces, per host, at least `MIN_INTERVAL = 1.0` seconds between
requests, caps a single company at `MAX_PAGES_PER_COMPANY = 8` pages, and
retries a failed request up to `MAX_RETRIES = 3` times with exponential
backoff. `robots.txt` is fetched once per host and cached for the run; a
disallowed path is skipped and counted, never fetched anyway. A 403, 429, or
a page whose first 2KB matches a block marker (`"captcha"`, `"cloudflare"`,
`"access denied"`, `"are you a robot"`, `"unusual traffic"`, `"request
blocked"`) means the crawler backs off from that host entirely for the rest
of the run and records it — there is no workaround, and building one is out
of scope for this project.

`SECTORRADAR_CONTACT` is mandatory, not a suggestion. `Settings.user_agent()`
raises a `ConfigError` — and `fetch` refuses to start at all — if it's unset,
because the crawler identifies itself with a real contact address on every
request specifically so a site owner who objects can reach a human. There is
no generic User-Agent fallback.

`geocode.py` applies the same discipline to Nominatim:
`NOMINATIM_MIN_INTERVAL = 1.0` second, matching Nominatim's published usage
policy exactly, described in the module as "not negotiable, and not worth
arguing with." swisstopo is tried first (better data for a Swiss segment
anyway) and Nominatim is the fallback only.

### Wall-clock estimate

`CANDIDATE_PATHS` holds 12 URLs to try per company — the homepage plus
services, about and blog paths in German, French and English — and
`MAX_PAGES_PER_COMPANY = 8` caps how many are actually kept. A 404 still costs
a request and still waits out `MIN_INTERVAL = 1.0` s, so budget close to 12
seconds per company rather than 8, plus response latency and the one-off
`robots.txt` fetch per host.

Companies are processed sequentially, so that spacing does not overlap. For
~150 sites from a cold cache, expect **25–35 minutes**. That was the measured
range on a real run of 143 companies. The tail is dominated by slow or
unresponsive hosts rather than by request volume.

A re-run against the same sites with nothing changed is far faster: pages whose
`content_sha` is unchanged are skipped without a request at all (see
[Re-run semantics](#re-run-semantics)).

## Re-run semantics

Every stage is idempotent — designed to be re-run against the same database
without redoing or undoing prior work:

- **`fetch`** hashes each page's cleaned text as `content_sha` and stores it
  on the `page` row. A page whose hash already matches what's on disk is
  counted as `unchanged` and never re-requested unless `--force` is passed.
  This is what makes routine re-runs cheap: an unchanged site costs nothing
  on a second `fetch`.

- **`extract --only-changed`** compares a company's current signature (the
  concatenation of its pages' `content_sha` values) against the signature
  recorded on its last extraction. If nothing changed, extraction is skipped
  for that company — no LLM call, no cost. `sectorradar run` always passes
  `only_changed=True` for exactly this reason; the standalone `extract`
  command defaults to `False` so a fresh run against pages that haven't
  changed hashes yet still extracts everything the first time.

- **`resolve`** skips any candidate that already carries a `resolved_to` or
  a `reject_reason` — re-running costs nothing and changes nothing for
  candidates already processed. Only genuinely new candidates from the
  latest `discover` get resolved.

- **`classify`** excludes companies whose `review_state` is not `pending`
  unless `force=True` is passed to the module. In other words, once a human
  set a company's `review_state` to anything other than `pending`, a later
  `classify` run leaves that decision alone rather than
  overwriting it with a fresh LLM tier — human review state is sticky by
  default. (Note: as of this writing the `classify` CLI subcommand doesn't
  expose a `--force` flag itself; forcing a re-classification of reviewed
  rows currently means calling `classify_mod.classify(..., force=True)`
  directly rather than through the CLI.)

- **`run`** chains discover → resolve → fetch → extract → classify →
  geocode and **commits after each stage** — not just at the end. This means
  an interrupted `run` (Ctrl-C, a crashed process, a lost SSH session) leaves
  the database in a consistent state after whichever stage last completed,
  and re-running `sectorradar run` resumes rather than restarting: `fetch`
  skips what's unchanged, `extract --only-changed` skips what hasn't moved,
  `resolve` skips already-resolved candidates. `run`'s own `KeyboardInterrupt`
  handler prints exactly this ("interrupted — the database is consistent,
  re-run to resume") rather than a traceback.

## Caches

- **`data/raw/<content_sha>.html`** — the raw HTML behind every fetched page,
  keyed by the hash of its cleaned text. Safe to delete entirely: the next
  `fetch` will simply re-request every page (the `page.content_sha` values in
  the database no longer have a matching file, so nothing short-circuits),
  which costs crawl time and politeness budget but no money. `extract` reads
  from these cached files via `fetch.page_texts`, so deleting them without
  re-fetching leaves `extract` with nothing to read for any company whose
  pages are gone.

- **`data/cache/geocode.json`** — a flat JSON map from a normalised address
  query to its result, including cached *misses* (a place that couldn't be
  found once won't be looked up again). Safe to delete: geocoding is free, so
  the only cost of clearing this cache is re-spending the wall-clock time
  described above, at the same 1 req/s Nominatim ceiling for anything
  swisstopo doesn't resolve.

- **`data/radar.db`** is not a cache — it is the dataset itself. See Backups
  below.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `fetch` (or `run`) refuses to start, error mentions `SECTORRADAR_CONTACT` | `SECTORRADAR_CONTACT` is unset in `.env` | Set it to a real contact address in `.env`; the crawler will not send an anonymous User-Agent |
| `extract`/`classify`/`websearch` error about missing LLM credentials | `GOOGLE_CLOUD_PROJECT` unset, or ADC not set up | Set `GOOGLE_CLOUD_PROJECT` in `.env` and run `gcloud auth application-default login` once |
| A site returns nothing and shows up in `blocked_hosts` | The site's bot-block (Cloudflare, a CAPTCHA, a 403/429) triggered | Back off — this is by design. Record it and move on; do not build a workaround. Consider adding the domain as a `seeds` entry with manually captured detail instead |
| `sectorradar stats` reports 0/0 or "no gold set defined" | The segment's `gold_set` list is empty | Add gold-set entries to the segment YAML — recall can't be measured against nothing |
| No companies show up on the map / `geocode` reports many `no address yet` | `extract` hasn't run yet, or found no address on the company's site | Run `extract` first; geocoding depends on an address `extract` put into `company_field`, and some sites genuinely never publish one |
| `error: no database at data/radar.db — run 'sectorradar init' first` | The database file doesn't exist yet | Run `uv run sectorradar init` |

## Backups

`data/radar.db` is the entire collected dataset — every company, claim,
tier, and human review decision — and it is gitignored by design (see
`AGENTS.md` §9: "never commit collected data"). That means it has no version
history of its own; there is no `git log` to fall back on if something
destructive happens to it. Before any operation that rewrites large parts of
it — a `--force` re-fetch or re-extract across the whole segment, a schema
migration, a bulk edit through the app — copy the file first:

```bash
cp data/radar.db data/radar.db.bak-$(date +%Y%m%d)
```

A plain file copy is sufficient; there's nothing else to snapshot alongside
it unless you also want to preserve `data/raw/` and `data/cache/` at that
point in time, which is worth doing before a destructive re-fetch since
those caches are what make the next `fetch` cheap.

## Sharing the result

One person runs the crawl; several people want to look at the answer; nobody
wants to run a server. That is an object-store problem.

### Why GCS rather than Cloud SQL or BigQuery

| | What it would cost | Why not |
|---|---|---|
| **Cloud SQL** | An always-on instance, a VPC or public IP, and a connection string in everybody's environment | There is exactly one writer and the whole dataset is 1.8 MB. A managed Postgres to serve a file that fits in a browser tab is a database bill and a patching obligation bought for nothing. |
| **BigQuery** | Per-query scanning, a schema per table, an ETL step | BigQuery earns its keep when the data is too big to hold in memory and the questions are unpredictable. This is 288 rows, and the questions are already answered in the export. |
| **GCS** | Storage of a couple of megabytes and a handful of reads | The pipeline already exports one self-contained JSON document, and the page is already static files. Publishing is a copy. |

The deciding argument is not cost, it is that the architecture already produces
the right artefact. `sectorradar export --format web` writes a document that a
browser reads with no server; putting that document in a bucket changes nothing
about how anything works. Reintroducing a database in the middle would undo the
property that makes the output shareable in the first place.

If this ever grows to a point where somebody wants to run ad-hoc SQL across
several years of snapshots, BigQuery becomes the right answer. That is a
different tool for a different question, and the snapshots are already being
kept for it.

**Speed**: the export is 1.8 MB, 239 KB gzipped, and it is baked into the page
at build time rather than fetched separately. From `europe-west6` to a machine
in Switzerland that is a single static asset over a few milliseconds of
latency. It loads faster than any database-backed view of the same data could.

### Setting up the bucket

Once, by whoever owns the project. Pin the project explicitly on every command
rather than relying on the ambient `gcloud config` default.

```bash
PROJECT=your-project-id
BUCKET=your-bucket-name

gcloud storage buckets create "gs://$BUCKET" \
  --project="$PROJECT" \
  --location=europe-west6 \
  --uniform-bucket-level-access \
  --public-access-prevention

gcloud storage buckets update "gs://$BUCKET" --project="$PROJECT" --versioning
```

`--public-access-prevention` is the important flag. This bucket must never be
readable by `allUsers`: the dataset is assembled from public pages, but a
published aggregate of a market with your own position marked in it is not
something to leave open, and a bucket made public by accident is not easily
made private again in anybody's memory.

### Giving somebody access

By email address, which is what makes this simpler than any of the
alternatives:

```bash
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --project="$PROJECT" \
  --member="user:colleague@example.com" \
  --role="roles/storage.objectViewer"
```

`objectViewer` is read-only. Nobody but the person running the crawl needs
write access, and nobody should have it.

### Publishing

```bash
make data          # export from the local database
make web           # build the site
make publish       # shows exactly what would be uploaded, writes nothing
make publish EXECUTE=1
```

The dry run is the default deliberately. Uploading to shared storage is
outward-facing and awkward to take back, so it is a thing you ask for rather
than a thing that happens.

### Just handing somebody the page

The built site is published beside the data, and it is static files. For
somebody who only wants to look, this is the whole handover — no repository, no
Python, no Node:

```bash
gcloud auth login
gcloud storage cp -r "gs://$BUCKET/site/<segment>/*" sectorradar --project="$PROJECT"
# open sectorradar/index.html
```

Each segment is published under its own `site/<slug>/`. A single `site/` was a
single slot, and publishing a second market silently replaced the first
market's page while both datasets sat in `data/` looking correct.

That the folder opens from the filesystem is not incidental. It is why the
build rewrites every asset URL to a relative one, and why there is a test that
fails when one comes out absolute.

### What a colleague does

```bash
git clone <repo> && cd sectorradar
make setup
# set SECTORRADAR_GCP_PROJECT and SECTORRADAR_GCS_BUCKET in .env
gcloud auth application-default login
make app
```

`make app` pulls the published document, builds the page and serves it at
`localhost:8080`. No database, no API keys, no crawl, no cost.

**They never need the segment definition.** `make app` asks the bucket what it
holds: one dataset and it takes that one, several and it lists them so they can
pick with `make app SEGMENT=<slug>`. This matters because the market most worth
sharing a result for is often one kept out of the repository — requiring the
reader to hold a config file they were never given would make the whole
arrangement circular. The published document carries the market's own
definition, which is what lets them interpret the figures at all.

### Credentials

Application Default Credentials, always. **Never create or download a
service-account key file for this.** There is nothing here that needs one, and
a `*.json` key sitting in a repository or a home directory is the most common
way a project like this leaks. If one already exists, treat it as compromised:
rotate it, then delete it.
