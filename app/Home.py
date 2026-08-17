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

    left, t1, t2, pending = st.columns(4)
    left.metric("Companies", data["total"])
    t1.metric("Tier 1", by_tier.get(1, 0))
    t2.metric("Tier 2", by_tier.get(2, 0))
    pending.metric("Pending review", by_review.get("pending", 0))

    if by_review.get("pending", 0):
        st.info(
            f"{by_review['pending']} companies are waiting for review — "
            "the Review page is the fastest quality lever available.",
            icon="👀",
        )

    if data["last_run"]:
        st.caption(f"Last discovery run finished {data['last_run']}")


main()
