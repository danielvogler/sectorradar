"""Overview: segment picker and headline counts.

Run with:  uv run --extra app streamlit run app/Home.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import queries

st.set_page_config(page_title="sectorradar", page_icon="🛰️", layout="wide")


def no_database_panel() -> None:
    st.title("🛰️ sectorradar")
    st.warning("No database yet.", icon="📭")
    st.markdown(
        """
        The explorer reads `data/radar.db`, which the pipeline creates. Nothing
        has built it yet.

        ```bash
        uv run sectorradar init
        uv run sectorradar run --segment agentic-ai-ch
        ```

        Then reload this page.
        """
    )


@st.cache_data(ttl=60)
def load_overview(slug: str, _mtime: float) -> dict[str, object]:
    """Cached on the database's mtime, so a pipeline run invalidates it."""
    return {
        "total": queries.company_count(slug),
        "by_tier": queries.counts_by_tier(slug),
        "by_review": queries.counts_by_review_state(slug),
        "last_run": queries.last_run(slug),
        "recall": queries.gold_set_recall(slug),
        "saturation": queries.saturation(slug),
    }


def main() -> None:
    if not queries.database_exists():
        no_database_panel()
        return

    st.title("🛰️ sectorradar")

    available = queries.segments()
    if not available:
        st.info(
            "The database exists but holds no segments yet. Run "
            "`uv run sectorradar run --segment agentic-ai-ch`.",
            icon="🌱",
        )
        st.metric("Companies", 0)
        return

    slugs = [s["slug"] for s in available]
    default = st.query_params.get("segment")
    index = slugs.index(default) if default in slugs else 0
    slug = st.sidebar.selectbox("Segment", slugs, index=index)
    st.query_params["segment"] = slug

    data = load_overview(str(slug), queries.db_mtime())
    by_tier = {int(r["tier"]): int(r["n"]) for r in data["by_tier"]}  # type: ignore[union-attr]
    by_review = {str(r["review_state"]): int(r["n"]) for r in data["by_review"]}  # type: ignore[union-attr]

    recall = data["recall"]

    left, t1, t2, pending, cov = st.columns(5)
    left.metric("Companies", data["total"])
    t1.metric("Tier 1", by_tier.get(1, 0))
    t2.metric("Tier 2", by_tier.get(2, 0))
    pending.metric("Pending review", by_review.get("pending", 0))
    cov.metric(
        "Gold-set recall",
        f"{recall['percent']}%" if recall["expected"] else "—",  # type: ignore[call-overload,index]
        help="Share of known-good companies this pipeline found on its own.",
    )

    if by_review.get("pending", 0):
        st.info(
            f"{by_review['pending']} companies are waiting for review — "
            "the Review page is the fastest quality lever available.",
            icon="👀",
        )

    # --- coverage -----------------------------------------------------------
    if recall["expected"]:  # type: ignore[index]
        st.subheader("Coverage")
        st.caption(
            f"Found {recall['found']} of {recall['expected']} companies in the gold set. "  # type: ignore[index]
            "Recall says nothing about precision — a run that returned every company "
            "in the country would score 100% — so read it beside the tier counts."
        )
        missing = recall["missing"]  # type: ignore[index]
        if missing:
            with st.expander(f"{len(missing)} gold-set companies were not found"):
                st.write(", ".join(missing))

    # --- saturation ---------------------------------------------------------
    runs = data["saturation"]
    if runs:
        st.subheader("Saturation")
        st.caption(
            "New-unique candidates per discovery run. When this flattens, the channel "
            "is exhausted — switch channel rather than rephrasing the query."
        )
        st.bar_chart(
            {
                "new unique": [int(r["new_unique_n"] or 0) for r in runs],  # type: ignore[union-attr]
                "total results": [int(r["results_n"] or 0) for r in runs],  # type: ignore[union-attr]
            }
        )

        by_source: dict[str, dict[str, int]] = {}
        for run in runs:  # type: ignore[union-attr]
            entry = by_source.setdefault(str(run["source"]), {"runs": 0, "results": 0, "new": 0})
            entry["runs"] += 1
            entry["results"] += int(run["results_n"] or 0)
            entry["new"] += int(run["new_unique_n"] or 0)

        st.dataframe(
            [
                {
                    "source": source,
                    "runs": stats["runs"],
                    "results": stats["results"],
                    "new unique": stats["new"],
                    "yield": (stats["new"] / stats["results"]) if stats["results"] else 0.0,
                }
                for source, stats in sorted(by_source.items())
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "yield": st.column_config.ProgressColumn(
                    "yield", min_value=0.0, max_value=1.0, format="%.0f%%"
                )
            },
        )

    if data["last_run"]:
        st.caption(f"Last discovery run finished {data['last_run']}")


main()
