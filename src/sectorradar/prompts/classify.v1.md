You are deciding whether one company belongs in a defined market segment, and
if so, where.

This judgement is the hard part of the whole exercise. Finding companies is
easy; drawing the boundary consistently is not. Apply the rule below as written
rather than your own sense of what the segment is "really" about.

## The inclusion rule

{inclusion}

## The tiers

{tiers}

## The company

Domain: {domain}
Summary: {one_liner}

Offerings recorded from its own website, each with the sentence that supports
it:

{offerings}

Other recorded facts:

{facts}

## What to produce

**`tier`** — the tier this company belongs to, or `null` if the evidence above
does not support placing it in any of them. `null` is the right answer when the
company plainly fails the inclusion rule, and also when the evidence is too
thin to tell. Do not reach for a tier to avoid returning nothing.

**`tier_rationale`** — one or two sentences saying *why*, referring to the
evidence above. This is read by a human reviewing the decision, so write it for
them: "Sells agent development as a named service on its services page" is
useful; "Appears to be a tier 1 company" is not. A rationale that does not cite
something specific is a rationale that cannot be checked.

**`relevance`** — 0 to 1, how central this segment is to what the company sells.
A firm whose entire business is this segment is near 1. A large integrator with
one small service line is near 0.2, even if it qualifies.

**`facets`** — tags within the fixed facets below. You may use a value not
listed if the evidence clearly supports it, but you may not invent a new facet,
and you may not tag something the evidence does not show.

{facets}

## Cautions

- Judge what the company *sells to clients*, not what it uses internally. A
  consultancy that uses LLMs to write its own proposals is not selling agent
  development.
- A technology partner badge is not a service. Neither is a client logo.
- Absence of evidence is evidence of absence here: the extraction step already
  read the site. If an offering is not listed above, the site did not clearly
  claim it.
- Do not consider anything about individual people.
