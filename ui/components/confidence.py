"""Confidence badge component."""

import streamlit as st

from ui.components.formatting import escape

_LEVELS = {
    "high": ("jm-badge-green", "High confidence"),
    "medium": ("jm-badge-amber", "Moderate confidence"),
    "low": ("jm-badge-red", "Limited data"),
    "very_low": ("jm-badge-red", "Very limited data"),
}


def render_confidence_badge(confidence: dict) -> None:
    """Render a confidence badge with explanation.

    Args:
        confidence: dict with keys 'level', 'precedent_count', 'explanation'.
    """
    level = confidence.get("level", "low")
    n = confidence.get("precedent_count", 0)
    explanation = confidence.get("explanation", "")

    css_class, label = _LEVELS.get(level, _LEVELS["low"])

    detail = f"{n} similar cases" if n > 0 else "directional only"
    html = (
        f'<span class="jm-badge {css_class}">'
        f"{escape(label)} &mdash; {escape(detail)}"
        f"</span>"
    )
    st.markdown(html, unsafe_allow_html=True)

    if explanation:
        st.caption(explanation)
