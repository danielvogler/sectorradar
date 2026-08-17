# Contributing to sectorradar

Thanks for your interest in contributing. This document covers how to set
up a development environment, the checks your change needs to pass, and
some project-specific conventions.

## Development Setup

sectorradar uses [`uv`](https://docs.astral.sh/uv/) for dependency and
environment management.

```bash
uv sync
uv run pre-commit install --install-hooks
cp .env.example .env
```

Fill in any values `.env` requires for your local setup before running
the pipeline or the Streamlit app. Never commit a filled-in `.env` file.

## The Pre-Commit Gate

Every change must pass the following four commands before it is
considered ready for review. This is the same gate `pre-commit` and CI
run, so running it locally before pushing saves round-trips:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

* `ruff check .` — linting
* `ruff format --check .` — formatting (run `uv run ruff format .` to fix)
* `mypy` — strict static type checking
* `pytest` — the test suite, with coverage

Do not skip or work around any of these with inline suppression comments
unless there is no reasonable alternative, and explain why in the pull
request description if you do.

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>: <subject>

<optional body>
```

Allowed types:

* `feat` — a new feature
* `fix` — a bug fix
* `refactor` — a code change that neither fixes a bug nor adds a feature
* `docs` — documentation only
* `test` — adding or correcting tests
* `chore` — tooling, dependencies, or other maintenance
* `perf` — a performance improvement
* `ci` — changes to CI configuration

Keep the subject line to 72 characters or fewer, written in the
imperative mood (`fix crawler timeout`, not `fixed` or `fixes`).

## Adding a Market Segment

Segments are defined declaratively in YAML files under `segments/`.
Adding a new segment normally requires only a new YAML file — no code
changes. See [`docs/adding-a-segment.md`](docs/adding-a-segment.md) for
the schema and a worked example. If your segment genuinely needs a code
change (a new classification rule, a new geocoder, and so on), open an
issue first to discuss the approach before submitting a large pull
request.

## Crawler Politeness

If you are adding or modifying a discovery source (anything that fetches
pages from the open web), the following rules are non-negotiable:

* **Respect `robots.txt`.** Check it before fetching, and honor
  disallow rules and crawl-delay directives for the host.
* **Rate-limit to 1-2 requests per second per host.** Do not open
  concurrent connections to the same host to get around this.
* **Send an identifying User-Agent** that names the tool and includes a
  contact address, for example:
  `sectorradar/0.1 (+https://github.com/danielvogler/sectorradar; contact: security@vogler-consulting.ch)`
* **Never work around a bot-block or a `403`.** If a site blocks the
  crawler, that is the site operator's decision — do not rotate user
  agents, use residential proxies, or otherwise disguise the request to
  bypass it.
* **Never collect personal data about individuals.** Discovery sources
  should extract information about companies (name, legal form, site,
  headcount estimates, and so on), not names, email addresses, or other
  personal details of the people who work there. See
  [`DATA.md`](DATA.md) for the full data-handling policy.

Pull requests that add a discovery source without addressing these
points will be asked to update before review continues.

## Pull Requests

* Keep pull requests focused on one change; unrelated cleanup belongs in
  a separate PR.
* Update `CHANGELOG.md` under `## [Unreleased]` for any user-facing
  change.
* Update relevant documentation alongside the code it describes.
* Fill in the pull request template completely, including the testing
  section.

## Reporting Bugs and Requesting Features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For security
vulnerabilities, do not open a public issue — see
[`SECURITY.md`](SECURITY.md) instead.
