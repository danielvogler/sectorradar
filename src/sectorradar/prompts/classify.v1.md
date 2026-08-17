You are deciding whether one company belongs in a defined market segment, and
if so, where.

This judgement is the hard part of the whole exercise. Finding companies is
easy; drawing the boundary consistently is not. Apply the rule below as written
rather than your own sense of what the segment is "really" about.

## The inclusion rule

{inclusion}

## The tiers

{tiers}

## Geography — check this first

This segment covers **{country}** only. Before considering tier at all, decide
whether this company has a real presence there: an office, a registered
entity, or an address. A company that merely *sells into* the country, lists it
among twenty others, or happens to rank in a search for it does **not** qualify.

Return `tier: null` when there is no evidence of presence in {country}. Say so
in the rationale. This is the most common reason to reject a company, and
getting it wrong is what fills a market map with offshore development agencies
that have never had a client there.

Recorded location for this company: **{location}**

If that says "none recorded", the extraction step read the site and found no
address in {country}. Treat that as meaningful evidence of absence, not as a
gap to be generous about.

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
- **A directory, listicle, marketplace or industry association is not a company
  in this segment.** A site whose content is *lists of other companies* — "top
  AI agencies in X", agency matchmaking, a members register — belongs at
  `tier: null` however much it talks about the subject.
- **A software product or platform sold as a subscription is not a services
  business**, unless the site also names a distinct consulting, development or
  training offering. Check the inclusion rule above for what this segment says
  about product companies.
- A large multinational vendor whose software the segment's companies *use* is
  not itself a member of the segment.
- Absence of evidence is evidence of absence here: the extraction step already
  read the site. If an offering is not listed above, the site did not clearly
  claim it.
- Do not consider anything about individual people.
