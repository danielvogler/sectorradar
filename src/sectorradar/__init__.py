"""sectorradar — turn a market segment into a structured, browsable dataset.

The package is split in two halves that never import each other:

* ``sectorradar`` (this package) is the pipeline. It discovers candidate
  companies, resolves them to canonical rows, fetches their sites, extracts
  structured profiles, classifies and geocodes them, and writes SQLite.
* ``app`` is a Streamlit front end that only ever reads the SQLite file.

``data/radar.db`` is the single interface between them.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
