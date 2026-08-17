"""Where the companies in this segment actually are.

Uses folium's marker clustering rather than a flat scatter layer, because the
underlying data is *genuinely* stacked: geocoding a company that gave only a
city places it at that city's centre, so forty-nine firms in Zürich share one
exact coordinate. A scatter layer cannot separate identical points at any zoom
— the pixels are the same pixels.

Clustering answers that directly. Overlapping markers collapse into a numbered
circle, the circle splits into smaller ones as you zoom, and markers that are
still identical at maximum zoom fan out on click ("spiderfy") so each one is
reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import folium
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import filters, queries

st.set_page_config(page_title="Map · sectorradar", page_icon=":round_pushpin:", layout="wide")

SWITZERLAND = (46.82, 8.23)
INITIAL_ZOOM = 8

#: Marker colour by tier. Folium's Icon takes named colours, not RGB.
TIER_COLOUR: dict[int, str] = {1: "red", 2: "orange", 3: "blue", 4: "gray"}
UNTIERED_COLOUR = "lightgray"


def popup_html(row: dict[str, object], tags: list[str]) -> str:
    """The card shown when a marker is clicked."""
    name = str(row["canonical_name"])
    domain = str(row["domain"])
    tier = f"Tier {row['tier']}" if row["tier"] else "unclassified"
    where = ", ".join(str(v) for v in (row.get("city"), row.get("canton")) if v) or "—"
    does = ", ".join(tags[:5]) or "—"
    summary = str(row.get("one_liner") or "")[:220]

    return (
        f"<div style='font-family:sans-serif;font-size:13px;min-width:220px'>"
        f"<b>{name}</b><br>"
        f"<span style='color:#666'>{tier} · {where}</span><br>"
        f"<a href='https://{domain}' target='_blank'>{domain}</a>"
        f"<p style='margin:6px 0'>{summary}</p>"
        f"<span style='color:#444'><i>{does}</i></span>"
        f"</div>"
    )


def main() -> None:
    st.title("Map")

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.")
        return

    state = filters.sidebar(segment, default_include_untiered=False)
    rows = filters.apply(state, require_coordinates=True)

    if not rows:
        st.info("No companies match these filters, or none have been geocoded yet.")
        return

    tags = queries.tags_by_company(segment)

    fmap = folium.Map(location=SWITZERLAND, zoom_start=INITIAL_ZOOM, tiles="cartodbpositron")
    cluster = MarkerCluster(
        # Split clusters right down to street level, and fan out anything still
        # sharing a coordinate at the deepest zoom.
        options={
            "maxClusterRadius": 45,
            "disableClusteringAtZoom": 15,
            "spiderfyOnMaxZoom": True,
            "showCoverageOnHover": False,
        }
    ).add_to(fmap)

    for row in rows:
        tier = row["tier"]
        folium.Marker(
            location=[float(row["lat"]), float(row["lon"])],  # type: ignore[arg-type]
            popup=folium.Popup(popup_html(row, tags.get(int(row["id"]), [])), max_width=320),  # type: ignore[call-overload]
            tooltip=str(row["canonical_name"]),
            icon=folium.Icon(
                color=TIER_COLOUR.get(int(tier), UNTIERED_COLOUR) if tier else UNTIERED_COLOUR,
                icon="briefcase",
                prefix="fa",
            ),
        ).add_to(cluster)

    st_folium(fmap, width=None, height=620, returned_objects=[])

    legend = " · ".join(f":{c}[●] Tier {t}" for t, c in ((1, "red"), (2, "orange"), (3, "blue")))
    st.caption(f"{len(rows)} companies. {legend}")
    st.caption(
        "Numbers are clusters — zoom in and they split. At full zoom, companies "
        "sharing an address fan out when you click the cluster."
    )

    stacked = queries.stacked_points(segment)
    if stacked:
        worst = stacked[0]
        with st.expander(
            f"{sum(int(s['n']) for s in stacked)} companies sit on a city centre, not their own address"
        ):
            st.caption(
                "A company whose website gives only a city is placed at that city's "
                "centre, so they stack. Re-running `sectorradar extract` picks up "
                "street addresses where a site publishes one — usually in the footer "
                "or the imprint."
            )
            st.dataframe(
                [{"city": s["city"], "companies on one point": s["n"]} for s in stacked],
                width="stretch",
                hide_index=True,
            )
            st.caption(f"Largest stack: {worst['n']} companies in {worst['city']}.")

    missing = queries.without_coordinates(segment)
    if missing:
        with st.expander(f"{len(missing)} companies have no coordinates and are not shown"):
            st.caption(
                "Their site gave no address, or the address could not be placed in "
                "Switzerland. Listed rather than dropped, so the map is not quietly "
                "missing part of the market."
            )
            st.dataframe(
                missing,
                width="stretch",
                hide_index=True,
                column_config={
                    "id": None,
                    "canonical_name": "Company",
                    "domain": "Domain",
                    "city": "City",
                    "canton": "Canton",
                    "tier": "Tier",
                },
            )


main()
