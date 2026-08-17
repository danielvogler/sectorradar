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


def radius_for(headcount: object) -> float:
    """Marker size in *pixels*, by headcount, on a gentle curve.

    Pixels, not metres. ScatterplotLayer measures radius in metres by default,
    which means a marker covers a fixed patch of ground and grows on screen as
    you zoom in — so two companies in the same city stay merged into one blob
    however far you zoom, which is precisely when you want them to separate.
    In pixels the marker is a fixed screen size and zooming pulls them apart.

    The curve is gentle because linear scaling makes one 400-person integrator
    swamp thirty ten-person consultancies, which is the opposite of what this
    map is for.
    """
    if not headcount:
        return 5.0
    return round(min(4.0 + 1.6 * (float(headcount) ** 0.5), 22.0), 1)


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
        tags_by_company = queries.tags_by_company(segment)
        points = [
            {
                "name": r["canonical_name"],
                "domain": r["domain"],
                "one_liner": (r["one_liner"] or "")[:160],
                "tier": f"Tier {r['tier']}" if r["tier"] else "unclassified",
                "does": ", ".join(tags_by_company.get(int(r["id"]), [])[:5]) or "—",
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
                        # Radius is a screen measurement, so markers separate as
                        # you zoom instead of staying a single blob per city.
                        radius_units="pixels",
                        stroked=True,
                        get_line_color=[255, 255, 255, 180],
                        line_width_min_pixels=1,
                        pickable=True,
                        opacity=0.75,
                    )
                ],
                tooltip={"text": "{name} — {tier}\n{domain}\n{does}\n{one_liner}"},
            )
        )
        st.caption(
            f"{len(points)} companies shown. Colour is tier, size is headcount estimate. "
            "Several companies often share a city — zoom in to separate them."
        )

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
