"""The review queue — the project's main quality lever.

At a few hundred rows you can look at all of them, and a human eye on every
company is worth more than any amount of prompt tuning. This page is built for
speed: one company at a time, every claim shown with the sentence and link
behind it, and a decision in one click.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import filters, queries

st.set_page_config(page_title="Review · sectorradar", page_icon="👀", layout="wide")


def render_company(segment: str, row: dict[str, object], reviewer: str) -> None:
    company_id = int(row["id"])  # type: ignore[call-overload]

    st.subheader(str(row["canonical_name"]))
    st.markdown(f"🔗 [{row['domain']}](https://{row['domain']})")
    if row.get("one_liner"):
        st.write(row["one_liner"])

    meta = " · ".join(
        str(v) for v in (row.get("city"), row.get("canton"), row.get("founded_year")) if v
    )
    if meta:
        st.caption(meta)

    proposed = row.get("tier")
    st.markdown(f"**Proposed tier:** {proposed if proposed else 'unclassified'}")
    if row.get("tier_rationale"):
        st.info(str(row["tier_rationale"]), icon="🧭")

    offerings = queries.offerings(company_id)
    if offerings:
        st.markdown("**Offerings, with the evidence behind each**")
        for offering in offerings:
            with st.container(border=True):
                st.markdown(f"**{offering['label']}**")
                st.markdown(f"> {offering['evidence_quote']}")
                st.caption(f"[source]({offering['evidence_url']})")
    else:
        st.warning("No offerings were extracted — the site did not clearly claim any.", icon="🕳️")

    tags = queries.tags(company_id)
    if tags:
        st.caption(" · ".join(f"{t['facet']}: {t['value']}" for t in tags))

    st.divider()

    note = st.text_input("Note (optional)", key=f"note-{company_id}")
    retier = st.selectbox(
        "Set tier",
        [None, 1, 2, 3, 4],
        index=0,
        format_func=lambda v: "keep proposed" if v is None else f"Tier {v}",
        key=f"tier-{company_id}",
    )

    accept, reject, info = st.columns(3)
    if accept.button("✅ Accept", key=f"a-{company_id}", width="stretch"):
        queries.save_review(
            segment, company_id, review_state="accepted", reviewer=reviewer, note=note, tier=retier
        )
        st.rerun()
    if reject.button("❌ Reject", key=f"r-{company_id}", width="stretch"):
        queries.save_review(
            segment, company_id, review_state="rejected", reviewer=reviewer, note=note, tier=retier
        )
        st.rerun()
    if info.button("❓ Needs info", key=f"n-{company_id}", width="stretch"):
        queries.save_review(
            segment,
            company_id,
            review_state="needs_info",
            reviewer=reviewer,
            note=note,
            tier=retier,
        )
        st.rerun()


def main() -> None:
    st.title("👀 Review")

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.", icon="🌱")
        return

    reviewer = st.sidebar.text_input("Reviewer", value="owner")
    pending = queries.companies(segment, review_states=["pending"])

    counts = {r["review_state"]: r["n"] for r in queries.counts_by_review_state(segment)}
    done = sum(int(v) for k, v in counts.items() if k != "pending")
    total = done + len(pending)

    if total:
        st.progress(done / total, text=f"{done} of {total} reviewed")

    if not pending:
        st.success("Nothing left in the queue.", icon="🎉")
        return

    render_company(segment, pending[0], reviewer)

    with st.expander(f"{len(pending) - 1} more waiting"):
        st.dataframe(
            [
                {"Company": r["canonical_name"], "Domain": r["domain"], "Tier": r["tier"]}
                for r in pending[1:]
            ],
            width="stretch",
            hide_index=True,
        )


main()
