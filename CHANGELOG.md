# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog][keep-a-changelog], and this
project adheres to [Semantic Versioning][semver].

## [Unreleased]

### Added

### Changed

### Fixed

### Removed

## [0.1.0] - 2026-08-17

### Added

* Initial release of the sectorradar pipeline: discover, resolve, fetch,
  extract, classify, geocode, review, and snapshot stages, producing a
  structured, evidence-backed database of companies for a given market
  segment and geography.
* YAML-defined market segments under `segments/`, so new segments can
  be added without code changes.
* SQLite storage (`data/radar.db`) as the single interface between the
  pipeline and the app, with per-claim provenance so every stored fact
  can be traced back to the evidence and source it was derived from.
* Streamlit application (`app/`) providing a filterable company table,
  a map view, and a review workflow for confirming or correcting
  low-confidence classifications.
* Swiss-focused geocoding via the swisstopo API, with OpenStreetMap
  Nominatim as a fallback for addresses swisstopo cannot resolve.

[keep-a-changelog]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html
[unreleased]: https://github.com/danielvogler/sectorradar/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/danielvogler/sectorradar/releases/tag/v0.1.0
