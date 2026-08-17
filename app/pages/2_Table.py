"""The filterable table, and the export button for whatever you filtered to."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import filters, queries

st.set_page_config(page_title="Table · sectorradar", page_icon="📋", layout="wide")

COLUMNS = [
    "canonical_name",
    "domain",
    "tier",
    "city",
    "canton",
    "one_liner",
    "headcount_est",
    "founded_year",
    "review_state",
    "relevance",
]


def to_csv(rows: list[dict[str, object]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> None:
    st.title("📋 Table")

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.", icon="🌱")
        return

    state = filters.sidebar(segment)
    rows = filters.apply(state)

    st.caption(f"{len(rows)} companies match.")

    if not rows:
        st.info("Nothing matches these filters.", icon="🔍")
        return

    st.dataframe(
        [{k: r.get(k) for k in COLUMNS} for r in rows],
        use_container_width=True,
        hide_index=True,
        column_config={
            "canonical_name": st.column_config.TextColumn("Company", width="medium"),
            "domain": st.column_config.LinkColumn(
                "Website", display_text=r"https?://(?:www\.)?([^/]+)"
            ),
            "tier": st.column_config.NumberColumn("Tier", width="small"),
            "city": "City",
            "canton": st.column_config.TextColumn("Canton", width="small"),
            "one_liner": st.column_config.TextColumn("What they do", width="large"),
            "headcount_est": st.column_config.NumberColumn("Headcount", width="small"),
            "founded_year": st.column_config.NumberColumn("Founded", width="small", format="%d"),
            "review_state": st.column_config.TextColumn("Review", width="small"),
            "relevance": st.column_config.ProgressColumn(
                "Relevance", min_value=0.0, max_value=1.0, format="%.2f"
            ),
        },
    )

    st.download_button(
        "Download this view as CSV",
        data=to_csv(rows),
        file_name=f"{segment}-filtered.csv",
        mime="text/csv",
    )


main()
