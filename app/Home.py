"""Overview: how many companies, what they do, and where they are.

Deliberately answers three questions and stops. Pipeline diagnostics — how many
results each query returned, whether a channel is exhausted — belong to whoever
is tuning the pipeline, not to somebody looking at the market, so they live
behind `sectorradar stats` and an expander at the bottom.

Run with:  uv run --extra app streamlit run app/Home.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import queries

st.set_page_config(page_title="sectorradar", page_icon=":satellite:", layout="wide")

#: The facet that answers "what kind of business is this?". Others (tech,
#: vertical, delivery model) are secondary and shown below it.
PRIMARY_FACET = "service_type"


def no_database_panel() -> None:
    st.title("sectorradar")
    st.warning("No database yet.")
    st.markdown(
        """
        The explorer reads `data/radar.db`, which the pipeline creates.

        ```bash
        uv run sectorradar init
        uv run sectorradar run --segment agentic-ai-ch
        ```
        """
    )


@st.cache_data(ttl=60)
def load(slug: str, _mtime: float) -> dict[str, object]:
    """Cached on the database's mtime, so a pipeline run invalidates it."""
    return {
        "total": queries.company_count(slug),
        "by_tier": queries.counts_by_tier(slug),
        "by_canton": queries.counts_by_canton(slug),
        "facets": {f: queries.counts_by_facet(slug, f) for f in queries.known_facets(slug)},
        "recall": queries.gold_set_recall(slug),
        "saturation": queries.saturation(slug),
        "last_run": queries.last_run(slug),
    }


def main() -> None:
    if not queries.database_exists():
        no_database_panel()
        return

    st.title("sectorradar")

    available = queries.segments()
    if not available:
        st.info("The database exists but holds no segments yet.")
        st.metric("Companies", 0)
        return

    slugs = [str(s["slug"]) for s in available]
    current = st.query_params.get("segment")
    index = slugs.index(current) if current in slugs else 0
    slug = str(st.sidebar.selectbox("Segment", slugs, index=index))
    st.query_params["segment"] = slug

    data = load(slug, queries.db_mtime())
    by_tier = {int(r["tier"]): int(r["n"]) for r in data["by_tier"]}  # type: ignore[union-attr]

    # --- how many -----------------------------------------------------------
    total, t1, t2, rest = st.columns(4)
    total.metric("Companies", data["total"])
    t1.metric("Tier 1", by_tier.get(1, 0), help="This segment is their primary offering")
    t2.metric("Tier 2", by_tier.get(2, 0), help="They also do it, among other things")
    rest.metric(
        "Wider pool",
        sum(n for t, n in by_tier.items() if t not in (1, 2)) + by_tier.get(0, 0),
        help="Tier 3-4 and companies not yet classified — the long tail.",
    )

    st.divider()

    # --- what they do -------------------------------------------------------
    left, right = st.columns([3, 2])

    with left:
        st.subheader("By business")
        facets: dict[str, list[dict[str, object]]] = data["facets"]  # type: ignore[assignment]
        primary = facets.get(PRIMARY_FACET) or []
        if primary:
            st.caption("What these companies sell, taken from their own websites.")
            total_companies = int(data["total"])  # type: ignore[call-overload]
            st.dataframe(
                [
                    {
                        "does": str(r["value"]).replace("_", " "),
                        "companies": int(r["n"]),
                        "share": (int(r["n"]) / total_companies) if total_companies else 0.0,
                    }
                    for r in primary
                ],
                width="stretch",
                hide_index=True,
                height=min(460, 36 * (len(primary) + 1)),
                column_config={
                    "does": st.column_config.TextColumn("Does", width="medium"),
                    "companies": st.column_config.NumberColumn("Companies", width="small"),
                    "share": st.column_config.ProgressColumn(
                        "Share", min_value=0.0, max_value=1.0, format="%.0f%%"
                    ),
                },
            )
        else:
            st.caption("No business categories yet — run `sectorradar classify`.")

        others = {f: v for f, v in facets.items() if f != PRIMARY_FACET and v}
        if others:
            with st.expander("Other breakdowns — technology, vertical, delivery model"):
                for facet, values in others.items():
                    st.markdown(f"**{facet.replace('_', ' ')}**")
                    st.caption(" · ".join(f"{r['value']} ({r['n']})" for r in values[:12]))

    # --- where they are -----------------------------------------------------
    with right:
        st.subheader("By canton")
        cantons = [r for r in data["by_canton"] if r["canton"] != "unknown"]  # type: ignore[union-attr]
        unknown = next(
            (int(r["n"]) for r in data["by_canton"] if r["canton"] == "unknown"),
            0,  # type: ignore[union-attr]
        )
        if cantons:
            st.dataframe(
                [
                    {"canton": r["canton"], "companies": r["n"], "tier 1-2": r["tier12"]}
                    for r in cantons
                ],
                width="stretch",
                hide_index=True,
                height=min(420, 36 * (len(cantons) + 1)),
            )
        if unknown:
            st.caption(
                f"{unknown} companies have no canton recorded — their site gave no "
                "address, or it could not be placed in Switzerland."
            )

    # --- pipeline health, out of the way ------------------------------------
    st.divider()
    recall = data["recall"]
    with st.expander("Pipeline health — coverage, saturation, last run"):
        if recall["expected"]:  # type: ignore[index]
            st.markdown(
                f"**Gold-set recall** {recall['percent']}% "  # type: ignore[index]
                f"({recall['found']}/{recall['expected']})"  # type: ignore[index]
            )
            st.caption(
                "Share of a hand-written list of known-good companies that the pipeline "
                "found on its own. It says nothing about precision — a run returning "
                "every company in the country would score 100% — and most of this "
                "particular list was also fed in as seeds, so treat it as a "
                "did-discovery-collapse check rather than a coverage measurement."
            )
            missing = recall["missing"]  # type: ignore[index]
            if missing:
                st.caption(f"Not found: {', '.join(missing)}")

        runs = data["saturation"]
        if runs:
            st.markdown("**Discovery yield by source**")
            st.caption(
                "New-unique candidates per run. When this flattens a channel is "
                "exhausted, and the answer is a different channel rather than a "
                "rephrased query."
            )
            by_source: dict[str, dict[str, int]] = {}
            for run in runs:  # type: ignore[union-attr]
                entry = by_source.setdefault(
                    str(run["source"]), {"runs": 0, "results": 0, "new": 0}
                )
                entry["runs"] += 1
                entry["results"] += int(run["results_n"] or 0)
                entry["new"] += int(run["new_unique_n"] or 0)
            st.dataframe(
                [
                    {
                        "source": source,
                        "runs": s["runs"],
                        "results": s["results"],
                        "new unique": s["new"],
                        "yield": (s["new"] / s["results"]) if s["results"] else 0.0,
                    }
                    for source, s in sorted(by_source.items())
                ],
                width="stretch",
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
