"""The overview: every company ranked by how sure we are it belongs here.

This is the page that makes reviewing all of them optional. Sorted by
certainty, the top is the part worth your attention and the bottom is visibly
junk — a much better use of an hour than an unordered queue.
"""

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
    "certainty",
    "confidence",
    "canonical_name",
    "domain",
    "tier",
    "does",
    "sells",
    "city",
    "canton",
    "one_liner",
    "evidence",
    "headcount_est",
    "founded_year",
    "review_state",
]


def to_csv(rows: list[dict[str, object]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def decorate(segment: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Add certainty, tags and offerings, then rank by certainty."""
    tags = queries.tags_by_company(segment)
    offerings = queries.offerings_by_company(segment)
    evidence = queries.evidence_strength(segment)

    decorated = []
    for row in rows:
        company_id = int(row["id"])  # type: ignore[call-overload]
        count = evidence.get(company_id, 0)
        score = filters.certainty(row, count)
        decorated.append(
            {
                **row,
                "certainty": score,
                "confidence": filters.certainty_label(score),
                "does": ", ".join(tags.get(company_id, [])[:6]),
                "sells": "; ".join(offerings.get(company_id, [])[:3]),
                "evidence": count,
            }
        )

    decorated.sort(key=lambda r: float(r["certainty"]), reverse=True)  # type: ignore[arg-type]
    return decorated


def main() -> None:
    st.title("📋 Overview")

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.", icon="🌱")
        return

    state = filters.sidebar(segment)
    rows = decorate(segment, filters.apply(state))

    floor = st.sidebar.slider(
        "Minimum certainty",
        0.0,
        1.0,
        value=float(st.query_params.get("minc", 0.0)),
        step=0.05,
        help="Raise this to hide the doubtful tail without reviewing it by hand.",
    )
    st.query_params["minc"] = str(floor)
    if floor > 0:
        rows = [r for r in rows if float(r["certainty"]) >= floor]  # type: ignore[arg-type]

    if not rows:
        st.info("Nothing matches these filters.", icon="🔍")
        return

    buckets: dict[str, int] = {}
    for row in rows:
        buckets[str(row["confidence"])] = buckets.get(str(row["confidence"]), 0) + 1
    summary = " · ".join(
        f"**{buckets[k]}** {k}" for k in ("high", "medium", "low", "doubtful") if k in buckets
    )
    st.caption(f"{len(rows)} companies — {summary}")
    st.caption(
        "Ranked by certainty: the classifier's relevance score, its tier, and how much "
        "cited evidence the extraction found. Start at the top; the tail is the tail."
    )

    st.dataframe(
        [{k: r.get(k) for k in COLUMNS} for r in rows],
        use_container_width=True,
        hide_index=True,
        column_config={
            "certainty": st.column_config.ProgressColumn(
                "Certainty", min_value=0.0, max_value=1.0, format="%.2f"
            ),
            "confidence": st.column_config.TextColumn("", width="small"),
            "canonical_name": st.column_config.TextColumn("Company", width="medium"),
            "domain": st.column_config.TextColumn("Domain", width="medium"),
            "tier": st.column_config.NumberColumn("Tier", width="small"),
            "does": st.column_config.TextColumn("Does", width="medium", help="Facet tags"),
            "sells": st.column_config.TextColumn(
                "Sells", width="large", help="Offerings extracted from their own site"
            ),
            "city": "City",
            "canton": st.column_config.TextColumn("Canton", width="small"),
            "one_liner": st.column_config.TextColumn("Summary", width="large"),
            "evidence": st.column_config.NumberColumn(
                "Cited", width="small", help="Offerings backed by a verbatim quote"
            ),
            "headcount_est": st.column_config.NumberColumn("Staff", width="small"),
            "founded_year": st.column_config.NumberColumn("Founded", width="small", format="%d"),
            "review_state": st.column_config.TextColumn("Review", width="small"),
        },
    )

    st.download_button(
        "Download this view as CSV",
        data=to_csv(rows),
        file_name=f"{segment}-ranked.csv",
        mime="text/csv",
    )


main()
