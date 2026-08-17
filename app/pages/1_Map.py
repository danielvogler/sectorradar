"""Where the companies in this segment actually are."""

from __future__ import annotations

import sys
from pathlib import Path

import pydeck as pdk
import streamlit as st
from pydeck.types import String

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import filters, queries

st.set_page_config(page_title="Map · sectorradar", page_icon="🗺️", layout="wide")

# Roughly the centre of Switzerland, zoomed to fit the country.
SWITZERLAND = pdk.ViewState(latitude=46.82, longitude=8.23, zoom=7.0)

#: A light Carto basemap, which needs no API token. Without a basemap
#: (`map_style=None`) the canvas is a flat grey rectangle, so the markers float
#: with no coastline, lakes or city names to place them against — which makes
#: the map unreadable however correct the coordinates are.
BASEMAP = "light"


def radius_for(headcount: object) -> float:
    """Marker size in *pixels*, by headcount, on a gentle curve.

    Pixels, not metres. ScatterplotLayer measures radius in metres by default,
    so a marker covers a fixed patch of ground and swells as you zoom — two
    companies in one city stay a single blob exactly when you want them apart.

    The units have to be passed as ``pydeck.types.String``. A bare Python
    string is compiled into a data accessor (``"@@=pixels"``), deck.gl cannot
    resolve it, and it silently falls back to metres — which is how this looked
    fixed while still being broken.

    The curve is gentle because linear scaling lets one 400-person integrator
    swamp thirty ten-person consultancies, the opposite of what this map is for.
    """
    if not headcount:
        return 5.0
    return round(min(4.0 + 1.5 * (float(headcount) ** 0.5), 20.0), 1)


def main() -> None:
    st.title("🗺️ Map")

    if not queries.database_exists():
        filters.no_database_panel()
        return

    segment = filters.pick_segment()
    if segment is None:
        st.info("No segments in the database yet.", icon="🌱")
        return

    state = filters.sidebar(segment, default_include_untiered=False)
    rows = filters.apply(state, require_coordinates=True)

    if not rows:
        st.info("No companies match these filters, or none have been geocoded yet.", icon="🔍")
    else:
        tags = queries.tags_by_company(segment)
        points = [
            {
                "name": r["canonical_name"],
                "domain": r["domain"],
                "where": ", ".join(str(v) for v in (r["city"], r["canton"]) if v) or "—",
                "tier": f"Tier {r['tier']}" if r["tier"] else "unclassified",
                "does": ", ".join(tags.get(int(r["id"]), [])[:4]) or "—",
                "lat": r["lat"],
                "lon": r["lon"],
                "colour": filters.colour_for(r["tier"]),
                "radius": radius_for(r["headcount_est"]),
            }
            for r in rows
        ]

        st.pydeck_chart(
            pdk.Deck(
                map_style=BASEMAP,
                initial_view_state=SWITZERLAND,
                layers=[
                    pdk.Layer(
                        "ScatterplotLayer",
                        data=points,
                        get_position="[lon, lat]",
                        get_fill_color="colour",
                        get_radius="radius",
                        # Literal, not an accessor — see radius_for().
                        radius_units=String("pixels"),
                        radius_min_pixels=4,
                        radius_max_pixels=24,
                        stroked=True,
                        get_line_color=[255, 255, 255, 220],
                        line_width_min_pixels=1,
                        pickable=True,
                    )
                ],
                tooltip={"text": "{name} — {tier}\n{where}\n{does}\n{domain}"},
            )
        )

        legend = " · ".join(
            f":{c}[●] Tier {t}" for t, c in ((1, "red"), (2, "orange"), (3, "blue"), (4, "grey"))
        )
        st.caption(f"{len(points)} companies. {legend}. Marker size is headcount estimate.")
        st.caption("Several companies share each city — zoom in and they separate.")

    missing = queries.without_coordinates(segment)
    if missing:
        with st.expander(f"⚠️ {len(missing)} companies have no coordinates and are not on the map"):
            st.caption(
                "Their site gave no address, or the address could not be placed in "
                "Switzerland. They are listed rather than dropped, so the map is not "
                "quietly missing a third of the market."
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
