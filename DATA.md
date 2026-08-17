# Data Sources and Licensing

This document covers the licensing and usage terms of the **data** that
sectorradar collects and helps you browse. It is separate from the
Apache-2.0 license that covers the sectorradar **code** (see
[`LICENSE`](LICENSE)); the two are governed independently, because the
sources feeding the pipeline carry their own, different terms.

## No Collected Data Ships With This Repository

sectorradar never commits collected data. The `data/` directory
(including `data/radar.db`, the SQLite file that is the sole interface
between the pipeline and the Streamlit app) is gitignored by design, and
no sample or seed database is shipped in this repository.

This is deliberate, not an oversight. A public repository that bundles a
scraped company dataset takes on the redistribution obligations of every
source that data was drawn from, and those sources have different,
sometimes incompatible, terms. Keeping the code and the data separate
means the code stays cleanly Apache-2.0, and you are responsible only
for the data you collect yourself, under the terms below.

## Source Terms

| Source | What it provides | Terms |
| --- | --- | --- |
| LINDAS SPARQL endpoint (Swiss Linked Data services, incl. Zefix data) | Legal name, legal form, purpose text, registered seat, UID, registration status | Published under a "Provide-the-Source" (`PDDL`/`CC` style "share-alike with attribution") designation: free to use and to build on, provided you name the source and carry that attribution into anything derived from it. |
| General web search | Candidate company names, domains, and descriptive snippets used to seed discovery | Governed by the terms of the search provider used; treat snippets as pointers to a company's own site, not as content to republish. |
| Job advertisements | Signals such as active hiring, role types, and approximate headcount growth | Governed by the terms of the job board or ATS the ad was read from; used only to inform classification and headcount estimates, not reproduced verbatim. |
| Agency directories (Clutch, GoodFirms, DesignRush, Sortlist, and similar) | Candidate company listings used as seeds to find company domains | Each directory has its own terms of service, and **you, the user running the tool, are responsible for respecting them.** sectorradar prefers using directory results only as seeds to locate a company's own domain, then crawling that company's own site for evidence, rather than mirroring directory content. |
| swisstopo geocoding API | Address-to-coordinate geocoding for Swiss addresses | Provided by the Swiss Federal Office of Topography under its published API terms; free for the described use cases, attribution appreciated. |
| OpenStreetMap Nominatim | Fallback geocoder when swisstopo has no match | Data licensed under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/); attribution to OpenStreetMap contributors is required, and Nominatim's own [usage policy](https://operations.osmfoundation.org/policies/nominatim/) (rate limits, no bulk geocoding) applies. |

## No Personal Data About Individuals

sectorradar does not store personal data about individuals. Where a
company's "team" or "about us" page is used as evidence, the pipeline
extracts only a headcount estimate (a count of visible profiles or
listed roles) — never names, photos, email addresses, or other contact
details of the people shown. This is a deliberate scope boundary, and it
is why sectorradar treats the resulting dataset as company-level
business information rather than personal data subject to Switzerland's
revDSG or the EU's GDPR.

If you extend the pipeline, keep this boundary intact: extraction and
classification logic should never key on, store, or output an
individual's name or contact information.

## A Note on Legal Advice

This document describes sectorradar's design choices and general
practice around the sources it reads. It is not legal advice. If you
plan to redistribute a dataset built with sectorradar, or you are
uncertain whether your use of a particular source or directory complies
with that source's terms, consult a lawyer familiar with data licensing
and, where relevant, Swiss and EU data protection law.
