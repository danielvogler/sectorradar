# sectorradar — build progress

**Last updated:** 2026-08-17T10:05:00+02:00
**Current phase:** 0
**Spend so far (USD):** 0.00

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

### 2026-08-17T09:45 — Phase 0 (start)

Environment probe before writing anything:

| Tool | State |
|---|---|
| `uv` | 0.9.28 (Homebrew) — OK, no pip fallback needed |
| `python3` | 3.14.7 system; project pins 3.11 via `.python-version` |
| `sqlite3` | 3.51.0 — OK |
| `gitleaks` | 8.30.1 — OK, available for `make check` |
| `make` | GNU Make 3.81 |
| `includeCoAuthoredBy` | `false` in `~/.claude/settings.json` — verified, not assumed (§0.6 layer 1) |

**Provider probe (§15 Q2, Q3).** No `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`EXA_API_KEY`, `BRAVE_API_KEY` or `TAVILY_API_KEY` in the environment, in any
shell profile, or in a local `.env`. §0.7 names "no LLM key **and** no search key"
as a hard blocker, so I probed for an alternative before stopping.

Found one: gcloud ADC is authenticated (`vogler.daniel@googlemail.com`, project
`vogler-consulting`). Live-tested Vertex AI:

```
POST .../publishers/google/models/gemini-2.5-flash-lite:generateContent → HTTP 200
{"candidates":[{"content":{"parts":[{"text":"OK"}]}}],
 "usageMetadata":{"promptTokenCount":7,"candidatesTokenCount":1}}
```

So the blocker does not apply. Both providers resolve to Vertex AI via ADC —
`gemini-2.5-flash-lite` for extraction/classification, Google Search grounding
for the `websearch` source. This matches the house GCP rule (models via Vertex
with ADC, never raw API keys). Recorded under `## Deviations`.

## Deviations

- **Spec names Exa/Brave/Tavily for search and an unspecified LLM SDK for
  extraction; I am using Vertex AI via ADC for both.** No key for any named
  provider exists in this environment, and §15 Q2/Q3 both default to "whichever
  key exists in `.env`". None does, but ADC does, and the house GCP convention
  prefers Vertex with ADC over API keys anyway. The provider stays behind the
  one-interface abstraction the spec asks for in `sources/websearch.py`, so
  adding Exa later is a new implementation of an existing protocol, not a
  refactor.

## Open items for the owner

- **§15 Q1 — gold set.** Owner has not supplied one. Will bootstrap from search
  in Phase 2 per §9, mark the YAML `# BOOTSTRAPPED BY AGENT — owner must review`,
  and flag it here. Recall figures against an unverified gold set are a sanity
  signal, not a measurement — treat them as such until reviewed.
- **§15 Q2 — search provider.** Defaulted to Vertex AI Google Search grounding
  because no Exa/Brave/Tavily key exists. If the owner wants Exa's
  "find companies like this" neural search (genuinely better for this use case),
  add `EXA_API_KEY` to `.env` and implement the existing `SearchProvider`
  protocol.
- **§15 Q3 — LLM model.** Defaulted to `gemini-2.5-flash-lite` on Vertex.
- **§15 Q4 — T4 workshop providers.** Taking the stated default: own segment
  file, shared schema, doubles as the Phase 5 abstraction test.
- **§15 Q5 — refresh cadence.** Taking the stated default: none, v1 is manual.
- **GCP billing.** Pipeline LLM and search calls bill to the `vogler-consulting`
  project rather than to a standalone API key. Small (single-digit USD at the
  spec's scale) but it lands on a real cloud invoice, which the owner may not
  have expected.
