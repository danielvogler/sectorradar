# Pull Request

## Summary

<!-- What does this change do, in a sentence or two? -->

## Motivation and Context

<!-- Why is this change needed? Link any related issue with "Closes #123". -->

## Type of Change

* [ ] Bug fix (non-breaking change that fixes an issue)
* [ ] New feature (non-breaking change that adds functionality)
* [ ] Breaking change (fix or feature that would change existing behavior)
* [ ] New or updated market segment (`segments/*.yaml` only)
* [ ] Documentation update
* [ ] Tooling / CI change

## Testing Done

<!-- Describe the tests you ran and how to reproduce them. -->

## Pre-Merge Checklist

* [ ] The four-command gate passes locally:
  `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
* [ ] Documentation has been updated to match this change, where relevant
* [ ] `CHANGELOG.md` has been updated under `## [Unreleased]`
* [ ] No `Co-Authored-By` trailer from an AI tool appears on any commit
      in this pull request
