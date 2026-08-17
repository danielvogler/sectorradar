"""Where the companies in this segment actually are."""

from __future__ import annotations

import sys
from pathlib import Path

import pydeck as pdk
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import filters, queries

st.set_page_config(page_title="Map · sectorradar", page_icon="🗺️", layout="wide")

# Roughly the centre of Switzerland, zoomed to fit the country.
SWITZERLAND = pdk.ViewState(latitude=46.80, longitude=8.23, zoom=6.9)


def radius_for(headcount: object) -> int:
    """Marker size by headcount, on a gentle curve.

    Linear scaling makes a 400-person integrator swamp thirty ten-person
    consultancies, which is the opposite of what this map is for.
    """
    if not headcount:
        return 1200
    return int(1000 + 220 * (float(headcount) ** 0.5))


def main() -> None:
    st.title("🗺️ Map")

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.", icon="🌱")
        return

    state = filters.sidebar(segment)
    rows = filters.apply(state, require_coordinates=True)

    if not rows:
        st.info("No companies match these filters, or none have been geocoded yet.", icon="🔍")
    else:
        points = [
            {
                "name": r["canonical_name"],
                "domain": r["domain"],
                "one_liner": r["one_liner"] or "",
                "tier": f"Tier {r['tier']}" if r["tier"] else "unclassified",
                "lat": r["lat"],
                "lon": r["lon"],
                "colour": filters.colour_for(r["tier"]),
                "radius": radius_for(r["headcount_est"]),
            }
            for r in rows
        ]

        st.pydeck_chart(
            pdk.Deck(
                map_style=None,
                initial_view_state=SWITZERLAND,
                layers=[
                    pdk.Layer(
                        "ScatterplotLayer",
                        data=points,
                        get_position="[lon, lat]",
                        get_fill_color="colour",
                        get_radius="radius",
                        pickable=True,
                        radius_min_pixels=4,
                        radius_max_pixels=40,
                    )
                ],
                tooltip={"text": "{name}\n{tier}\n{domain}\n{one_liner}"},
            )
        )
        st.caption(f"{len(points)} companies shown. Colour is tier, size is headcount estimate.")

    # Rows without coordinates are listed rather than dropped: a map that
    # silently omits a third of the market is worse than no map.
    missing = queries.without_coordinates(segment)
    if missing:
        with st.expander(f"⚠️ {len(missing)} companies have no coordinates and are not on the map"):
            st.dataframe(
                missing,
                use_container_width=True,
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
