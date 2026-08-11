"""Zillow-style range bar for fine exposure visualization."""

import streamlit as st

from ui.components.formatting import format_eur_short, severity_color


def render_range_bar(
    p25: int | float,
    median: int | float,
    p75: int | float,
    min_val: int | float,
    max_val: int | float,
    precedent_count: int = 0,
) -> None:
    """Render a horizontal range bar with P25-P75 band and median marker.

    The bar shows:
    - Full track: min to max (thin gray line)
    - Solid band: P25 to P75 (colored, "where 50% of similar fines fell")
    - Marker: median (dark line)
    - Labels below: P25, Median, P75
    """
    if max_val <= 0 or max_val == min_val:
        st.caption("Insufficient data for range visualization.")
        return

    spread = max_val - min_val
    pct_p25 = max(0, min(100, (p25 - min_val) / spread * 100))
    pct_med = max(0, min(100, (median - min_val) / spread * 100))
    pct_p75 = max(0, min(100, (p75 - min_val) / spread * 100))
    band_width = max(1, pct_p75 - pct_p25)

    # Ensure labels don't overlap: min 12% apart
    if pct_med - pct_p25 < 12:
        pct_p25_label = max(0, pct_med - 12)
    else:
        pct_p25_label = pct_p25
    if pct_p75 - pct_med < 12:
        pct_p75_label = min(100, pct_med + 12)
    else:
        pct_p75_label = pct_p75

    med_color = severity_color(median)

    html = f"""
    <div class="jm-range-container">
        <div class="jm-range-track"></div>
        <div class="jm-range-band" style="
            left: {pct_p25}%;
            width: {band_width}%;
            background: linear-gradient(90deg, #059669, {med_color});
        "></div>
        <div class="jm-range-marker" style="
            left: {pct_med}%;
            background: var(--header-navy);
        "></div>
        <span class="jm-range-label" style="left: {pct_p25_label}%; color: #059669;">
            {format_eur_short(p25)}
        </span>
        <span class="jm-range-label" style="left: {pct_med}%; font-weight: 700; color: var(--header-navy);">
            {format_eur_short(median)}
        </span>
        <span class="jm-range-label" style="left: {pct_p75_label}%; color: {med_color};">
            {format_eur_short(p75)}
        </span>
    </div>
    <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--text-secondary); margin-top: 22px;">
        <span>Min: {format_eur_short(min_val)}</span>
        <span>P25 — Median — P75</span>
        <span>Max: {format_eur_short(max_val)}</span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

    if precedent_count > 0:
        st.caption(f"Exposure range based on **{precedent_count}** matching precedents")
