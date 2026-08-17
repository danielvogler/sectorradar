# Security Policy

## Supported Versions

sectorradar is currently pre-1.0 and released on a rolling `0.1.x` line.
Security fixes are made against the latest `0.1.x` release.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

If you believe you have found a security vulnerability in sectorradar,
please report it privately. **Do not open a public GitHub issue.**

Email **[security@vogler-consulting.ch](mailto:security@vogler-consulting.ch)**
with:

* A description of the vulnerability and its potential impact
* Steps to reproduce it, including any proof-of-concept code or requests
* The affected version, commit hash, or branch
* Any suggested mitigation, if you have one

You should receive an acknowledgement within 5 business days. We will keep
you informed as we investigate and work on a fix.

## Scope

The following are in scope for security reports:

* The pipeline in `src/sectorradar/` (discovery, resolution, fetching,
  extraction, classification, geocoding, review, and snapshot stages),
  including how it handles untrusted input from external sites and APIs
* The Streamlit application in `app/`, including any code that renders
  or queries data from `data/radar.db`
* How the project declares, pins, and updates its dependencies

The following are explicitly out of scope:

* Data collected by a user running the tool against their own segment
  definitions — sectorradar does not operate a hosted service or store
  any user's collected data
* The content, availability, or behavior of third-party sites and APIs
  that the tool reads from (registries, search engines, directories,
  geocoding services); report issues with those services to their owners
* Vulnerabilities that require an attacker to already have local access
  to a machine running sectorradar with a maliciously crafted segment
  YAML file the user chose to run

## No Secrets in This Repository

No credentials, API keys, or tokens are committed to this repository.
Configuration is supplied via environment variables (see `.env.example`).
[gitleaks](https://github.com/gitleaks/gitleaks) runs as part of
`pre-commit` and again in CI on every push and pull request to catch
accidental secret commits before they reach the default branch.

If you discover a secret committed to the repository's history, please
report it using the same private channel above rather than opening a
public issue, since the fix requires coordinated history rewriting and
credential rotation.

## Coordinated Disclosure

We ask that you give us **90 days** from the date of your report before
publicly disclosing the vulnerability, to allow time to investigate,
develop, and release a fix. We will credit reporters who wish to be
credited in the release notes once a fix ships, unless you prefer to
remain anonymous.
