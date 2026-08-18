# Adding a segment

> **Reference, not process.** This page is what each field means. For *how* to
> arrive at a good segment — the questions to ask first, how a near-miss
> becomes an exclusion rule, how to size a vocabulary — see
> [segments/AGENTS.md](../segments/AGENTS.md), which is written to be worked
> through with somebody.

A segment is one YAML file in `segments/`. Nothing under `src/` should need to
change to add one — if it does, the abstraction is wrong and the abstraction is
what to fix.

`segments/ai-assurance-ch.yaml` is the proof of that promise, not just an
example. It was added with zero changes to any module under `src/`, and it is
a genuinely different segment rather than `pilates-zurich.yaml` with the nouns
swapped: its `inclusion` rule admits training providers and solo consultants
that `pilates-zurich` deliberately excludes, and its `facets` describe how
training is delivered rather than what software gets built. Read both files
side by side before writing a third.

## The `Segment` model

Every key below is validated by the `Segment` pydantic model in
`src/sectorradar/config.py` with `extra="forbid"` — an unknown top-level key,
or an unknown key nested under `geo` or a `gold_set` entry, fails validation
rather than being silently ignored. This is the complete set of legal keys.

```yaml
# Required. Kebab-case, and must match the filename exactly:
# segments/my-segment.yaml must declare slug: my-segment.
slug: my-segment

# Required. Free text, shown in the UI and CLI output.
name: My Segment, Some Country

# Required.
geo:
  country: CH          # required, exactly 2 characters (ISO country code)
  cantons: null         # optional, defaults to null. null = all cantons.
                         # A list like [ZH, VD] narrows discovery and stats,
                         # not the classifier — inclusion still decides.

# Required, min 20 characters. Injected verbatim into the classifier prompt.
# See "Writing inclusion" below — this is the actual hard part.
inclusion: >
  Include a company if it offers X as a named service on its own website.
  Exclude Y.

# Required. Keys must be exactly 1, 2, 3, 4 — no more, no fewer allowed as
# keys, though you need not use all four if a segment genuinely has fewer
# tiers (an unused tier number is fine to omit).
tiers:
  1: "The primary offering"
  2: "A broader business that also does this"
  3: "A tangential business with a line of it"
  4: "Occasional or incidental"

# Optional, defaults to [1, 2]. Must reference only tiers 1-4. Controls which
# tiers get fetched, extracted and geocoded — the main cost lever.
enrich_tiers: [1, 2]

# Optional, defaults to {}. Facet names are fixed once written; the values
# listed are a starting vocabulary, not a closed one. See "Facets" below.
facets:
  facet_name: [value_one, value_two, value_three]

# Optional, defaults to {}. One entry per discovery source. Any key not
# listed here (e.g. urls:, queries:, purpose_terms:) is accepted freely,
# because SourceConfig allows extra keys — each source module reads the keys
# it cares about and ignores the rest. enabled: defaults to false.
sources:
  seeds:
    enabled: true
    urls: []            # see "sources" below for the shape of an entry
  websearch:
    enabled: true
    queries: []
  lindas:
    enabled: true
    purpose_terms: []

# Optional, defaults to []. Each entry: domain (required, min 3 chars) and
# expected_tier (optional, 1-4). See "gold_set" below.
gold_set:
  - domain: example.ch
    expected_tier: 1
```

Do not invent keys beyond this list. If a source needs a new configuration
key (say, a `max_results` for `websearch`), that is a legitimate addition —
`SourceConfig` allows extra keys by design — but it belongs in the relevant
`sources/*.py` module's own reading of `segment.source(name)`, not in the
`Segment` model itself.

## Writing `inclusion`

This string is not documentation — it is copied verbatim into the prompt the
classifier sends to the LLM for every company. It is the single piece of
prose that determines tiering, and writing a crisp boundary is the actual
hard work of adding a segment. Everything else in the file is bookkeeping.

A good example, from `ai-assurance-ch.yaml`:

```yaml
inclusion: >
  Include a company if it offers, as a named service on its own website,
  independent evaluation, testing or auditing of AI systems: model
  validation, LLM red-teaming, bias and robustness assessment, or
  EU AI Act and FINMA model-risk readiness work.
  Exclude companies that only build or integrate AI systems, however
  carefully, and however much they mention responsible AI.
  Exclude general cybersecurity firms with no AI-specific testing service.
  Exclude individual freelancers with no registered company.
  Exclude companies with no Swiss presence.
```

This works because every clause is a test the model can apply to a page it
just read: "is this a named service on the company's own website", "is there
a services arm", "is there a Swiss presence". Each inclusion and exclusion
clause draws a line a reader could actually walk up to and check.

A weak version of the same segment:

```yaml
inclusion: >
  Include companies that are into AI and agents.
```

This produces inconsistent tiering because "into AI and agents" is not a
test — it is a vibe. Two runs of the same model against the same page can
land on different sides of it, because nothing in the sentence tells the
model what to *check for*. A vague inclusion rule doesn't just admit more
noise, it admits *inconsistent* noise: the same borderline company can flip
between tier 2 and "excluded" between runs, which makes recall and tier
counts unstable in a way that has nothing to do with the underlying market
changing. Write inclusion the way you would write an acceptance test:
concrete, checkable clauses, not adjectives.

## `tiers`

Tier keys must be numbered 1 through 4 — the `Segment` validator rejects
anything else. You do not have to define all four if a segment truly only
needs, say, three ranks, but the keys you do use must come from that set.

A good tier description names what a reader would see on the company's own
site, ordered from "this is their whole business" (1) down to "this is a
minor or occasional part of what they do" (4). Compare the two segments:
`ai-assurance-ch` tier 1 is "AI assurance, evaluation or model risk is the
primary offering"; `pilates-zurich` tier 1 is "A dedicated Pilates studio — it is
the primary business" — same shape, different domain. Tier 4 in each file is
correspondingly the weakest signal worth keeping at all (a solo consultant or
an occasional workshop), not "everything else" — anything that fails every
tier is out of scope entirely and `classify` should return no tier for it.

## `enrich_tiers`

`enrich_tiers` decides which tiers actually get crawled (`fetch`), have a
structured profile extracted (`extract`), and get geocoded (`geocode`).
Companies at a tier outside this list stay as unenriched candidates —
classified, but never fetched or extracted. This is the main cost lever in
the project: `fetch` and `geocode` are free but `extract` and `classify` are
LLM calls (see `docs/operations.md` for the pricing detail), and `extract`
only runs against companies whose tier appears in `enrich_tiers`.

`ai-assurance-ch` enriches `[1, 2]` and leaves tier 3/4 as candidates only. A
segment might enrich `[1, 2, 3]` instead, where its tier 3
(a consultancy running workshops alongside its main business) is still
worth a full profile. There is no default answer — set it to the tiers whose
detail you actually intend to use.

## `facets`

**Both the facets and their values are closed.** The classifier picks from what
you declare and may invent neither. Values outside the list are counted as
`tags_out_of_vocabulary` and dropped, and unknown facet names are logged and
skipped.

That was not always true, and the reason it changed is worth knowing before you
loosen it: with an open vocabulary the model coined 130-odd service types
including "gala dinner", "podcast" and "magazine". A facet nobody can filter on
is not a facet.

Two shapes are accepted:

```yaml
facets:
  vertical: [finance, insurance, pharma]        # values only

  service_type:                                 # values plus the words that
    pentest: ["penetrationstest", "pentest"]    # ground each one
    incident_response: ["notfall", "forensik"]
```

The second shape is what makes a segment portable. A tag is only kept when it
can be traced to something the site said, and the words that prove it are
market-specific: `pentest` never appears on a page offering
*Penetrationstests*. Without them, a value falls back to a small built-in list
and then to the value itself — adequate in English, poor in anything else, and
the failure is silent. The tag is simply dropped and the table looks empty.

Two numbers to read after a run. `tags_out_of_vocabulary` means the model wants
words you have not declared; look at what it proposed and decide whether to
adopt them. `tags_ungrounded` means your vocabulary and the market's language
have come apart, and the fix is evidence words rather than more values.

Sizing: fifteen to twenty-five values for a facet people will filter on. Seven
was tried here and put half the market in one value.

## `sources`

Each source is a module in `src/sectorradar/sources/`, registered under the
name used as its key in `sources:`. `discover --source <name>` runs exactly
one.

- **`seeds`** — a hand-curated list under `urls:`, either bare strings or
  mappings with `url`, `name`, `city`, `canton`, `note`. This is the highest
  precision channel there is, and for tier 1 it beats every automated one:
  the segment file's own comments say the owner's referral graph and the
  firms a client mentioned considering, or that came up in a shortlist, belong
  here and are the
  single highest-value edit available to a new segment file. Realistic yield:
  one candidate per URL you type, no more, no less — it doesn't discover
  anything on its own.

- **`websearch`** — runs each string in `queries:` through a configured
  search provider (`vertex_grounding` by default, or `exa` / `brave` /
  `tavily` if their API keys are set). Write queries in every language the
  segment's market actually operates in — `ai-assurance-ch` runs its queries in
  German, French, Italian and English, plus city-scoped variants, because a
  German-only query set silently misses Romandie and Ticino. Realistic
  yield: up to 20 results per query, dominated by whatever the search
  provider's index already ranks well, so it's a breadth channel, not a
  precision one — expect real duplicates and irrelevant hits that `resolve`
  and `classify` have to filter out.

- **`lindas`** — sweeps the Swiss commercial register's purpose text
  (`purpose_terms:`) via the LINDAS SPARQL endpoint. Be honest about its one
  hard limitation: the register carries no website field. Every row this
  source produces arrives with `raw_url: None`, and `resolve.py` cannot turn
  that into a company — it rejects the candidate with reason `"no usable
  URL"`. This is not a bug to fix; a purpose-clause match without a domain is
  a lead for a human, not something the pipeline can promote automatically.
  High recall on small GmbHs with no marketing presence, brutal precision —
  a generic IT purpose clause matches thousands of unrelated firms.

There are two more source names your segment YAML can enable —
`jobads` (`keywords:`) and `directories` (`sites:`) — visible in
`ai-assurance-ch.yaml`. Be honest that directory sites (Clutch, GoodFirms,
Sortlist and similar) bot-block scrapers routinely; `AGENTS.md` states the
project's standing policy here explicitly: when a directory blocks the
crawler, record it and fall back to using whatever it already yielded as
seeds — never build a workaround.

## `gold_set`

The gold set is the list you check discovery against on every run, via
`sectorradar stats`. Recall against it — how many of these known-good domains
the pipeline actually found — is the single most important number in the
project, because saturation and tier counts tell you what the pipeline found
but recall is the only number that tells you what it *missed*.

The circularity warning matters more than it looks like it should: if your
gold set was assembled by running the same `websearch` queries the pipeline
uses for discovery, a high recall number is partly measuring "did the
pipeline find things it already knew to look for", not "does this pipeline
find the market". `ai-assurance-ch.yaml`'s own comments are explicit about
this — its gold set was drawn from a *different* search engine than the
configured `websearch` provider, which reduces overlap but does not
eliminate it, and `expected_tier` there is a judgement call from a search
snippet, not from reading each site. Treat a gold set assembled this way as a
sanity signal — "did discovery collapse this run" — not a measurement of true
coverage. The gold set a segment actually deserves is a list assembled from a
channel discovery doesn't use: your own referral graph, the firms that come up
when somebody asks you who else does this, or a list from someone who knows the
market. `pilates-zurich.yaml` keeps its gold set deliberately small for
exactly this reason — a padded gold set built the same way as discovery
would make its recall figure look meaningful when it isn't.

## Try it

Once `segments/my-segment.yaml` exists and validates, work through the
pipeline stage by stage so a mistake surfaces early rather than after a full
`run`:

```bash
uv run sectorradar discover --segment my-segment --source seeds
```

```bash
uv run sectorradar resolve --segment my-segment
```

```bash
uv run sectorradar fetch --segment my-segment
```

```bash
uv run sectorradar extract --segment my-segment
```

```bash
uv run sectorradar classify --segment my-segment
```

```bash
uv run sectorradar geocode --segment my-segment
```

```bash
uv run sectorradar stats --segment my-segment
```

Each stage prints its own counts, so a broken `inclusion` clause or an
overly narrow `seeds` list shows up immediately as "0 new" or "0 classified"
rather than three stages later. Once the individual stages look right,
`sectorradar run --segment my-segment` chains all of discover through
geocode in one call.

## Troubleshooting

- **"is not valid YAML"** — the error names the file and the underlying YAML
  parser's complaint (bad indentation, an unquoted colon, a tab character).
  Fix the syntax the message points at; `load_segment` refuses to guess.

- **"is not a valid segment — `<field>`: `<message>`"** — pydantic validation
  failed, and the message names the exact field path, e.g.
  `tiers: tiers must be numbered 1-4, got [5]` or
  `geo.country: String should have at least 2 characters`. This is the
  `extra="forbid"` behaviour in action too: a typo'd top-level key like
  `enrich_tier:` (missing the `s`) fails with an "extra fields not permitted"
  message rather than being silently ignored.

- **"declares slug '`x`' but the file is named '`y`.yaml'"** — the `slug:`
  value inside the file must match the filename stem exactly. Slugs must also
  be kebab-case: lowercase letters, digits and single hyphens only
  (`^[a-z0-9]+(-[a-z0-9]+)*$`). `My-Segment`, `my_segment` and `my--segment`
  all fail this pattern.

- **"no segment file at `segments/x.yaml` — available segments: ..."** — the
  slug passed to `--segment` doesn't match any file in `segments/`. The error
  lists what does exist.

- **A source silently yields nothing** — check the CLI output of `discover`
  for `0 found`; the source modules log a warning (`seeds.empty`,
  `websearch.no_queries`, `lindas.no_terms`) when their required list
  (`urls`, `queries`, `purpose_terms`) is empty, rather than failing the run.
