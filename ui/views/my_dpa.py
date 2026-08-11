"""Tab 2: My DPA — DPA behavioral profile page.

Displays full enforcement intelligence for a selected jurisdiction.
Uses services/dpa_profiles.py for all data access.
"""

import streamlit as st
import psycopg

from services.dpa_profiles import (
    DPAProfile,
    generate_dpa_profile,
    get_all_jurisdictions,
)
from ui.components.cards import render_precedent_card, render_stat_card
from ui.components.formatting import (
    format_eur,
    format_pct,
    trend_arrow,
    escape,
)


def render(conn: psycopg.Connection) -> None:
    """Render the My DPA tab."""
    st.markdown("### My DPA")
    st.markdown("Behavioral profile of any EU data protection authority.")

    jurisdictions = get_all_jurisdictions(conn)
    if not jurisdictions:
        st.warning("No jurisdiction data available.")
        return

    options = [j["jurisdiction"] for j in jurisdictions]
    labels = {
        j["jurisdiction"]: f"{j['jurisdiction']} ({j['total_cases']} cases)"
        for j in jurisdictions
    }

    # Pre-select from org profile if available
    profile = st.session_state.get("org_profile")
    default_idx = 0
    if profile and profile.jurisdiction in options:
        default_idx = options.index(profile.jurisdiction)

    selected = st.selectbox(
        "Select jurisdiction",
        options,
        index=default_idx,
        format_func=lambda x: labels.get(x, x),
    )

    if not selected:
        return

    with st.spinner("Loading DPA profile..."):
        dpa = generate_dpa_profile(conn, selected)

    if not dpa:
        st.warning(f"No enforcement data for {selected}.")
        return

    _render_stats_row(dpa)
    _render_details(dpa)
    _render_recent_cases(dpa)


def _render_stats_row(dpa: DPAProfile) -> None:
    """Top-level stats in 4 columns."""
    cols = st.columns(4)
    with cols[0]:
        render_stat_card("Total Decisions", f"{dpa.total_cases:,}")
    with cols[1]:
        render_stat_card("Median Fine", format_eur(dpa.median_fine), "#1B2A4A")
    with cols[2]:
        render_stat_card("Largest Fine", format_eur(dpa.max_fine), "#DC2626")
    with cols[3]:
        arrow = trend_arrow(dpa.trend_direction)
        render_stat_card("Trend", f"{arrow} {dpa.trend_direction.capitalize()}")


def _render_details(dpa: DPAProfile) -> None:
    """Articles, sectors, cooperation, and trend details."""
    col_art, col_sec, col_trend = st.columns(3)

    with col_art:
        st.markdown("##### Top Articles Enforced")
        for a in dpa.top_articles[:6]:
            pct = format_pct(a["pct"])
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:3px 0;border-bottom:1px solid var(--border-warm)">'
                f'<span>{escape(a["article"])}</span>'
                f'<span class="jm-number" style="font-size:0.85rem">'
                f'{a["count"]} ({pct})</span>'
                f"</div>",
                unsafe_allow_html=True,
            )

    with col_sec:
        st.markdown("##### Top Sectors Fined")
        for s in dpa.top_sectors[:5]:
            pct = format_pct(s["pct"])
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:3px 0;border-bottom:1px solid var(--border-warm)">'
                f'<span>{escape(s["sector"])}</span>'
                f'<span class="jm-number" style="font-size:0.85rem">'
                f'{s["count"]} ({pct})</span>'
                f"</div>",
                unsafe_allow_html=True,
            )

    with col_trend:
        st.markdown("##### Yearly Trend")
        if dpa.yearly_trend:
            import pandas as pd

            df = pd.DataFrame(dpa.yearly_trend)
            df = df[df["year"] >= 2019]
            if not df.empty:
                st.line_chart(df.set_index("year")["median"], height=180)

    # ---- Cooperation Credit ----
    if dpa.cooperation_credit:
        cc = dpa.cooperation_credit
        st.markdown("##### Cooperation Impact")
        st.markdown(
            f'<div class="jm-card">'
            f"Cooperating with {escape(dpa.jurisdiction)}'s DPA reduces the median fine by "
            f'<strong class="jm-number">{cc["reduction_pct"]:.0f}%</strong> '
            f'(based on {cc["n_cooperated"] + cc["n_not_cooperated"]} cases).<br/>'
            f'<span style="color:var(--text-secondary);font-size:0.85rem">'
            f'Cooperated: {format_eur(cc["median_cooperated"])} median · '
            f'Not cooperated: {format_eur(cc["median_not_cooperated"])} median'
            f"</span></div>",
            unsafe_allow_html=True,
        )

    # ---- Intent Split ----
    if dpa.intent_split:
        intentional = dpa.intent_split.get("intentional", 0)
        negligent = dpa.intent_split.get("negligent", 0)
        total = intentional + negligent
        if total > 0:
            st.markdown(
                f"**Intent split:** {intentional} intentional "
                f"({intentional / total:.0%}) vs "
                f"{negligent} negligent ({negligent / total:.0%})"
            )


def _render_recent_cases(dpa: DPAProfile) -> None:
    """Show the 5 most recent fined cases."""
    if not dpa.recent_cases:
        return
    st.markdown("##### Recent Decisions")
    for c in dpa.recent_cases:
        render_precedent_card(
            title=c["title"],
            jurisdiction=dpa.jurisdiction,
            fine_amount=c["fine"],
            articles=c["articles"],
            date=c.get("date"),
        )
