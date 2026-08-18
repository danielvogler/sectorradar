# Writing a segment

You are helping somebody define a market. This file is the whole job: a segment
is one YAML file here, and everything downstream — what gets searched, what
counts as in, how the tables are built, what the page says — comes from it. No
code changes. If you find yourself wanting one, the abstraction is wrong and
that is the bug to report.

Read this before writing any YAML. The failure mode is not a syntax error, it
is a file that runs cleanly and produces a useless dataset a day later.

## Interview first, write second

Do not start from the YAML. Start by asking, and keep asking until you could
argue the boundary with somebody who disagrees. The questions that matter:

1. **What is the market, in one sentence they would say out loud?**
   "Digital marketing agencies in Switzerland" is a start, not an answer.
2. **What are they going to *do* with the list?** Compare their own offering?
   Find who to approach? Understand where a market is thin? The purpose decides
   the boundary. A list for competitive positioning excludes firms too small to
   matter; a list for partnerships includes exactly those.
3. **Name five companies that are obviously in.** These become seeds and gold
   set. If they cannot name five, the segment is not yet a segment.
4. **Name three that are *nearly* in but should be out, and why.** This is the
   most valuable question you will ask. The "why" is the inclusion rule.
5. **What language do these companies sell in?** Not the country's official
   languages — what their websites are actually written in.
6. **How would a buyer find them?** Their search terms, not yours.

Write the file only when you can answer all six.

## `name` — this becomes the page headline

Not an internal label. It is the `<h1>` on the result, the browser tab, and the
first thing anybody you share the page with reads.

```yaml
name: Pilates studios, Zürich
name: AI assurance, evaluation and model risk, Switzerland
```

Convention is **`<what they do>, <where>`**. The page splits on the last comma
and sets the region on its own line under the subject, so a name written that
way typesets correctly from a phone to a wide monitor. A name that is a slug,
a single word, or shorter than eight characters is rejected at load — those
type-check and then greet everybody who opens the result.

## The inclusion rule is the most important text in the repository

It goes verbatim into the classifier prompt. Every tier, every exclusion, every
figure on the page follows from it. Write it as instructions to a careful
colleague doing the shortlist by hand.

```yaml
inclusion: >
  Include a company if it offers, as a named service on its own website,
  paid media management, SEO, content marketing or marketing automation
  for client organisations.
  Exclude in-house marketing teams and brand-side employers.
  Exclude pure web-design studios with no ongoing marketing service.
  Exclude freelancers with no registered company.
  Exclude companies with no Swiss presence.
```

What makes it work:

It has two audiences, and they want the same thing. It is injected verbatim
into the classifier prompt, *and* shown under the headline as the definition
every figure on the page is relative to. Instructions a careful colleague could
follow serve both; a description of a market serves neither, and a rule with no
`Include` in it is rejected at load.

- **"as a named service on its own website"** — the standard is what the site
  says, not what the company might do. Keep that phrase or one like it.
- **Exclusions are most of the value.** Three or four sentences beginning
  "Exclude" will do more for precision than any amount of query tuning. Take
  them from question 4.
- **Name the edge you are unsure about** and pick a side. An ambiguous rule
  produces an inconsistent dataset, which is worse than a rule you later
  change — re-tiering is cheap, re-crawling is not.

Tiers are the same job at finer grain: tier 1 is "this is what they are", tier
4 is "they touch it". Write them so somebody could sort a company without
asking you.

## Facets: the vocabulary the tables are built from

This is the part most likely to go wrong quietly, and the question to hold in
mind is: **what would the user want to filter by, and could two competitors
genuinely differ on it?**

**Size the list deliberately.** Too short and everybody looks identical — this
project shipped seven service types and put half the market in one of them,
which told nobody anything. Too long and the table stops being scannable.
Fifteen to twenty-five values is usually right. Aim for the smallest vocabulary
in which two real competitors come out different.

**Supply evidence words.** A tag is only kept if it can be traced to something
the site said, so write facets as `value: [words that prove it]`:

```yaml
facets:
  service_type:
    seo: ["seo", "suchmaschinenoptimierung", "référencement", "sichtbarkeit"]
    paid_media: ["sea", "google ads", "paid media", "performance marketing"]
    content: ["content marketing", "redaktion", "storytelling", "texterstellung"]
    automation: ["marketing automation", "hubspot", "crm", "lead nurturing"]
```

Rules for the words:

- **In the languages the market sells in.** `seo` will not match a page that
  says *Suchmaschinenoptimierung*. This is the single most common reason a
  vocabulary silently produces an empty table.
- **Stems beat whole words.** `automat` catches *automation*, *Automatisierung*
  and *automatisiert*. `optimier` catches four German inflections.
- **Nothing shorter than four characters unless it is a real term.** Matching
  is on word boundaries, but short tokens still overreach: `rag` matched
  *leverage*, *storage* and the German *fragen*, and reported 148 companies
  doing retrieval augmentation when about 20 did.
- **A value with no words falls back to the value itself**, which is adequate
  in English and poor elsewhere. Fine for `vertical: [finance, retail]`. Not
  fine for anything compound or abbreviated.

Facets other than `service_type` can be plain lists where the value is the word
people actually use. `vertical` should stay aligned with
`sectorradar.industries.INDUSTRIES` so a tag and a case study's sector can be
counted together.

## Queries: how the market is found

```yaml
sources:
  websearch:
    enabled: true
    queries:
      - "Digitalagentur Marketing Schweiz"
      - "SEO Agentur Zürich"
      - "agence marketing digital Suisse romande"
      - "performance marketing agency Switzerland"
```

- **Query in every language the market sells in.** A German-only list misses
  Romandie and Ticino entirely, and you will not notice, because the result
  still looks like a full dataset.
- **Query by city as well as by country.** National queries return national
  players; city queries surface the smaller firms that make a market map worth
  having.
- **Add channels that find firms before they market themselves**: job ads for
  the roles this work needs, partner directories, association membership lists.
- Thirty to sixty queries is a reasonable first pass. Watch the `new` column in
  `sectorradar stats` — when queries stop returning new companies, the channel
  is saturated and more of the same will not help.

## Seeds and the gold set

`seeds` are companies you already know, used as starting points. `gold_set` is
how you find out whether discovery works: recall against it is reported on
every run.

**Keep them mostly separate.** A gold set drawn from the seed list measures the
seed list. `sectorradar stats` reports "reached unaided" — entries an automated
source found without being handed the domain — and that is the number to read.

The best gold set is the user's own knowledge of the market: the firms that come
up whenever somebody asks who else does this, the ones a colleague mentioned,
the ones on a shortlist they have seen. Ask for it explicitly. It beats every
automated channel and takes them five minutes.

## Before you call it done

- [ ] The inclusion rule has at least two `Exclude` sentences taken from real
      near-misses.
- [ ] Every `service_type` value has evidence words, in every language the
      market sells in.
- [ ] Queries cover the languages and at least four cities.
- [ ] The gold set has entries that are *not* in the seed list.
- [ ] `uv run sectorradar run --segment <slug>` completes.
- [ ] `tags_ungrounded` in the classify output is not enormous. If it is, the
      vocabulary and the words the market uses have come apart — fix the
      evidence words before anything else.
- [ ] Read twenty rows of the result. If the boundary is wrong you will see it
      immediately, and `sectorradar classify` re-runs without re-crawling.

## Things that are not yours to put here

- **Nothing personal.** These files are meant to be shareable; that is the
  point of the abstraction. Which companies belong to the person running the
  tool goes in `<slug>.local.yaml`, which is gitignored and merged over this
  file at load time.
- **No credentials.** Providers and keys live in `.env`.
