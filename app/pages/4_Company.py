"""One company in full, with the provenance of every recorded claim."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import filters, queries

st.set_page_config(page_title="Company · sectorradar", page_icon="🏢", layout="wide")


def main() -> None:
    st.title("🏢 Company detail")
    st.caption(
        "Everything recorded about one company, and where each claim came from. "
        "Pick one below, or arrive here from a link on another page."
    )

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.", icon="🌱")
        return

    rows = queries.companies(segment)
    if not rows:
        st.info("No companies in this segment yet.", icon="🌱")
        return

    labels = {int(r["id"]): f"{r['canonical_name']} ({r['domain']})" for r in rows}
    ids = list(labels)

    # No default selection. Opening on whichever company happens to be first
    # makes the page look like it is *about* that company, which is confusing
    # and gives one arbitrary firm a whole screen of the dashboard.
    preselected = st.query_params.get("company")
    chosen = int(preselected) if preselected and preselected.isdigit() else None
    if chosen not in ids:
        chosen = None

    company_id = st.selectbox(
        "Company",
        [None, *ids],
        index=0 if chosen is None else ids.index(chosen) + 1,
        format_func=lambda i: "— pick a company —" if i is None else labels[i],
    )

    if company_id is None:
        st.query_params.pop("company", None)
        st.info(
            "Pick a company above to see its profile, the offerings extracted from "
            "its site, and the source URL behind every claim.",
            icon="👆",
        )
        return

    st.query_params["company"] = str(company_id)

    record = queries.company(int(company_id))
    if record is None:
        st.error("That company is no longer in the database.")
        return

    row = next(r for r in rows if int(r["id"]) == int(company_id))

    st.subheader(str(record["canonical_name"]))
    st.markdown(f"🔗 [{record['domain']}](https://{record['domain']})")
    if record.get("one_liner"):
        st.write(record["one_liner"])

    left, middle, right = st.columns(3)
    left.metric("Tier", row["tier"] or "—")
    middle.metric("Headcount est.", record.get("headcount_est") or "—")
    right.metric("Founded", record.get("founded_year") or "—")

    if row.get("tier_rationale"):
        st.info(str(row["tier_rationale"]), icon="🧭")

    st.divider()
    st.markdown("### Offerings")
    offerings = queries.offerings(int(company_id))
    if offerings:
        for offering in offerings:
            with st.container(border=True):
                st.markdown(f"**{offering['label']}**")
                st.markdown(f"> {offering['evidence_quote']}")
                st.caption(
                    f"[{offering['evidence_url']}]({offering['evidence_url']}) · extracted {offering['extracted_at']}"
                )
    else:
        st.caption("None recorded.")

    st.markdown("### Recorded facts and their sources")
    facts = [f for f in queries.fields(int(company_id)) if not str(f["field"]).startswith("_")]
    if facts:
        st.dataframe(
            facts,
            width="stretch",
            hide_index=True,
            column_config={
                "field": "Field",
                "value": "Value",
                "source_url": st.column_config.LinkColumn("Source"),
                "evidence_quote": "Evidence",
                "confidence": st.column_config.NumberColumn("Confidence", format="%.2f"),
                "extractor": "Extractor",
                "extracted_at": "Extracted",
            },
        )
    else:
        st.caption("Nothing extracted yet — run `sectorradar extract`.")

    tags = queries.tags(int(company_id))
    if tags:
        st.markdown("### Tags")
        st.caption(" · ".join(f"**{t['facet']}**: {t['value']}" for t in tags))


main()
