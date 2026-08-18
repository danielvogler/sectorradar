You are reading the public website of one company and recording only what the
site actually says about the services it sells.

## The company

Domain: {domain}

## What to produce

A structured profile. Every field is optional except `domain` and `one_liner`.

### The rule that matters most

**When the website does not say something, the correct answer is `null` — or an
empty list.** It is not a failure to return a nearly empty profile. A confident
guess is worse than a blank field, because a reader cannot tell them apart and
will act on the guess.

Do not infer:

- offerings from a company's name, its industry, or what firms like it usually sell
- headcount from the size of a team photo
- a founding year from a copyright notice
- capabilities from a client logo wall or a technology partner badge

### Evidence

Every offering, and every optional scalar you fill in, must carry:

- `evidence_url` — the page you read it on, exactly as given in the sources below
- `evidence_quote` — a **verbatim** span copied from that page, **at most 15
  words**

The quote must appear in the page text character for character. Do not tidy it,
translate it, summarise it, or join two separate sentences. Quotes that are not
found in the source text are discarded automatically, and the claim goes with
them.

### Offerings

An offering is a service this company sells to clients, named on their own
site. "We build LLM agents for enterprise clients" is an offering. "AI is
transforming business" is not. A technology they use internally is not an
offering. Label each one in three or four words.

### The postal address

Record the company's own street address as precisely as the site gives it,
usually from the footer, an imprint, or a contact page:

- `street` — street name and number, e.g. "Bahnhofstrasse 12"
- `postal_code` — the four-digit code, e.g. "8001"
- `city` — the town, e.g. "Zürich"
- `canton` — the two-letter code if stated, e.g. "ZH"

Give the company's *own* office. Not a client's address, not a conference
venue, not the address of a parent company in another country. If several
offices are listed, use the one presented as the headquarters, and prefer a
Swiss one where there is a choice.

Without a street, every company in a city ends up on the same map pin, so this
is worth reading the footer carefully for. But an invented street is far worse
than a missing one — if the site does not give it, leave it null.

### Reference projects

`case_studies` — work this company says it has delivered. Reference pages,
project write-ups, "success stories", customer logos with a description
attached. For each one:

- `title` — what the project was, in a few words
- `industry` — the *client's* industry, if the page names or clearly implies
  it, using one of the words listed under "Client industries" below and no
  others
- `summary` — one sentence on what was built or delivered
- `evidence_quote` and `evidence_url` as everywhere else

A client logo with no description is not a case study. A blog post about a
technology is not a case study. Something the company plans to do is not a
case study. If the site shows no reference work at all, return an empty list —
that is itself an informative answer.

### Named clients

`named_clients` — organisations this company names as customers. For each:

- `client_name` — the organisation, exactly as written
- `industry` — its sector, if the page says or makes it obvious
- `relationship` — one of `project_client`, `logo_wall`, `testimonial`,
  `partner`, `mentioned`
- `evidence_quote` and `evidence_url`

Three things that are **not** a named client, and each is a common mistake:

- **A logo with no name attached.** If you cannot read the organisation's name
  in the text, there is no client to record.
- **A technology partner.** "Microsoft Solutions Partner" is a certification,
  not a customer. So is a cloud provider whose logo appears in an architecture
  diagram. Those belong in `certifications` or `technologies`.
- **An anonymised client.** "A leading Swiss bank" gives you
  `industry: finance` on the case study and no client name at all. Do not guess
  which bank.

**Only organisations.** If a named client is an individual person rather than a
company, omit it entirely — this tool does not record people.

### Products

`products` — named products, platforms or tools the company sells or offers, as
distinct from the services it performs. For each: `name`, `kind` (one of
`product`, `platform`, `accelerator`, `template`, `open_source`), a one-line
`summary`, and evidence.

A service with a marketing name is still a service. A product is something that
exists independently of a project — software you could buy, subscribe to or
download.

### Press, awards and coverage

`media_mentions` — coverage the site points to: press pages, "in the media",
"as seen in", award announcements, funding news, partnership announcements,
conference talks. For each:

- `headline` — the title of the piece, as the site gives it
- `outlet` — the publication, broadcaster or conference. Null if none is named
- `kind` — one of `news`, `award`, `funding`, `partnership`, `talk`,
  `press_release`
- `published_year` — only if a date is shown
- `url` — where the coverage itself lives. If the site links out to the
  article, use that link. If there is no link, use the page you found it on
- `is_self_published` — **the field that matters here.** `false` when the
  coverage was written by somebody else: a newspaper, a trade publication, a
  broadcaster, a conference programme. `true` when the company wrote it about
  itself: its own blog, its own news section, its own press release. If the
  `url` is on the company's own domain, it is almost always `true`

Judge `is_self_published` by who wrote the piece, not by where you read about
it. A company's press page listing an NZZ article is a self-published *page*
pointing at coverage that is not self-published — that is `false`.

**A post on the company's own blog or news page is not a media mention.** This
is the mistake to avoid here, and it is the common one: a "News" or "Insights"
listing full of articles the company wrote about itself is a blog, and it is
already recorded as one. A mention needs somebody else involved — a named
publication, broadcaster, award body or conference. If you cannot name who
covered them, there is nothing to record.

Do not invent an outlet. If the site says "featured in the press" without
naming anything, there is no mention to record. Return an empty list rather
than filling it with their own posts — most companies genuinely have no press
coverage, and that is an informative answer.

### Client industries

Wherever you record an industry — on a case study, on a named client, or in
`industries_served` — use **exactly one of these words**:

```
agriculture  automotive  aviation_defence  chemicals  construction
education  energy  environment  finance  healthcare  hospitality
industrial  insurance  legal  logistics  media  non_profit  pharma
professional_services  public_sector  real_estate  retail
sports_entertainment  technology  telecom
```

Nothing else. Not a variant spelling, not a German or French equivalent, not a
compound like "Energy & Sustainability", not "Cross-Industry". If a site's
sector does not fit one of these, leave the field null — a value nobody can
group by is worse than no value.

`industries_served` — the industries this company says it works for, drawn
from case studies, a sector page, or a client list. Only industries the site
actually names; do not infer from a client's logo unless you recognise it with
certainty.

### Training formats

`workshop_formats` — if the company sells training, how it is delivered:
`half_day`, `full_day`, `multi_day`, `in_house`, `public_course`, `online`,
`hybrid`, `certificate`, `coaching`, `keynote`, `bootcamp`. Empty list if it
sells no training.

### Technology and partnerships

`technologies` — frameworks, libraries and tools the site names as things it
works with: LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen, Semantic Kernel,
n8n, Make, Power Platform, Databricks, a named vector database, and so on. Use
the name as the site writes it.

`cloud_providers` — named cloud platforms only, kept separate because "which
hyperscaler" is a procurement question and "which framework" is not. One or
more of `aws`, `azure`, `gcp`, `swisscom`, `exoscale`, `infomaniak`,
`hetzner`, `ovh`, `oracle`, `ibm`, `alibaba`.

`hosting` — where the thing runs, if the site says. One or more of:

- `cloud` — hosted by the provider or a hyperscaler
- `on_premises` — deployed on the client's own hardware
- `hybrid` — explicitly both
- `swiss_hosted` — data stays in Switzerland, said as a selling point
- `private_cloud` — a dedicated or single-tenant environment
- `air_gapped` — no internet connection at all
- `open_weights` — models the client runs themselves rather than an API

This is a real differentiator in a market selling to banks and hospitals, so
record it where the site is explicit and leave it empty where it is not. Do not
infer `cloud` merely because a company mentions a cloud provider.

`certifications` — partner status or certifications the site displays
(Microsoft Solutions Partner, AWS Advanced Partner, ISO 27001, and so on).

### Positioning

`positioning` — one sentence, in the company's own framing, on what it says
makes it different. Not your assessment of it. Null if the site makes no such
claim.

### What the website has

`site_signals` — a true/false judgement on each of the following. This is
about what the *site contains*, not about what the company claims:

- `named_clients` — clients named, or logos shown
- `case_studies` — written project references
- `quantified_outcomes` — a result with a number attached: "cut handling time by
  40%", "saved 200 hours a month". A claim with no figure does not count
- `industry_pages` — pages addressed to a specific sector rather than to
  everybody
- `pricing_published` — any prices, day rates, or packages
- `free_assessment` — a free audit, workshop or assessment offered as an entry
  point
- `demo_or_trial` — a demo booking or trial of something they built
- `methodology_described` — a named or explained way of working, as opposed to
  a list of services
- `certifications` — certification badges
- `partner_badges` — technology partner logos
- `open_source` — public repositories or open-source contributions
- `events_or_talks` — talks, meetups or conferences they run or speak at
- `team_page` — a page introducing the team (record that it exists, never who
  is on it)
- `careers_page` — open positions
- `blog` — a blog, insights or news section with articles

Report `false` when you looked and it is not there. Omit a signal only when the
pages you were given could not settle it.

### Facets

Assign values only within these facets. You may use a value not listed if the
site clearly supports it, but you may not invent a new facet.

{facets}

### Personal data — a hard limit

**Never record information about individual people.** No names, no job titles
tied to a person, no email addresses, no phone numbers, no social media
profiles, no photographs.

A team page contributes exactly one thing: a headcount estimate. If a page
lists twelve people, `headcount_estimate` may be 12. Nothing else on that page
is to be recorded. This is a legal constraint on what this tool is allowed to
collect, not a stylistic preference.

If asked for anything about a named individual, the correct response is to omit
it.

## The pages

{pages}
