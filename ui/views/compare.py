"""Tab 4: Compare DPAs — side-by-side comparison of enforcement authorities."""

import streamlit as st
import psycopg
import pandas as pd

from services.dpa_profiles import compare_dpas, get_all_jurisdictions
from ui.components.formatting import format_eur


def render(conn: psycopg.Connection) -> None:
    """Render the Compare DPAs tab."""
    st.markdown("### Compare DPAs")
    st.markdown("Side-by-side comparison of enforcement authorities across jurisdictions.")

    jurisdictions = get_all_jurisdictions(conn)
    if not jurisdictions:
        st.warning("No jurisdiction data available.")
        return

    options = [j["jurisdiction"] for j in jurisdictions if j["cases_with_fine"] >= 5]

    selected = st.multiselect(
        "Select DPAs to compare (2-5)",
        options,
        default=options[:3] if len(options) >= 3 else options,
        max_selections=5,
    )

    if len(selected) < 2:
        st.info("Select at least 2 jurisdictions to compare.")
        return

    with st.spinner("Loading profiles..."):
        profiles = compare_dpas(conn, selected)

    if not profiles:
        st.warning("No data for selected jurisdictions.")
        return

    _render_comparison_table(profiles)
    _render_charts(profiles)
    _render_top_articles(profiles)


def _render_comparison_table(profiles: list) -> None:
    """Stats table for selected DPAs."""
    rows = []
    for p in profiles:
        rows.append({
            "DPA": p.jurisdiction,
            "Cases": p.total_cases,
            "Fined Cases": p.cases_with_fine,
            "Total Fines": p.total_fines_eur,
            "Median Fine": int(p.median_fine),
            "Max Fine": p.max_fine,
            "Trend": p.trend_direction.capitalize(),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.format({
            "Total Fines": "EUR {:,.0f}",
            "Median Fine": "EUR {:,.0f}",
            "Max Fine": "EUR {:,.0f}",
        }),
        hide_index=True,
        use_container_width=True,
    )


def _render_charts(profiles: list) -> None:
    """Bar charts comparing key metrics."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### Median Fine")
        data = {p.jurisdiction: int(p.median_fine) for p in profiles}
        df = pd.DataFrame({"DPA": data.keys(), "Median Fine (EUR)": data.values()})
        st.bar_chart(df.set_index("DPA"))

    with col2:
        st.markdown("##### Total Fines")
        data = {p.jurisdiction: int(p.total_fines_eur) for p in profiles}
        df = pd.DataFrame({"DPA": data.keys(), "Total Fines (EUR)": data.values()})
        st.bar_chart(df.set_index("DPA"))


def _render_top_articles(profiles: list) -> None:
    """Top articles per DPA for comparison."""
    st.markdown("##### Top Enforced Articles per DPA")
    cols = st.columns(min(len(profiles), 5))

    for col, p in zip(cols, profiles):
        with col:
            st.markdown(f"**{p.jurisdiction}**")
            for a in p.top_articles[:5]:
                st.caption(f"{a['article']} — {a['count']} ({a['pct']}%)")
