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
