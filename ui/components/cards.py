"""Reusable card components: precedent cards, stat cards, factor tags."""

import streamlit as st

from ui.components.formatting import escape, format_eur, severity_color


def render_precedent_card(
    title: str,
    jurisdiction: str | None,
    fine_amount: int | None,
    articles: list[str] | None,
    similarity: float | None = None,
    date: str | None = None,
) -> None:
    """Render a single precedent case card."""
    fine_str = format_eur(fine_amount)
    color = severity_color(fine_amount)
    arts_str = ", ".join((articles or [])[:5]) or "N/A"
    sim_str = f" · Similarity: {similarity:.0%}" if similarity else ""
    date_str = f" · {escape(date)}" if date else ""

    html = (
        f'<div class="jm-card jm-card-hover">'
        f"<strong>{escape(title)}</strong><br/>"
        f'<span class="jm-number" style="color:{color}">{fine_str}</span>'
        f" · {escape(jurisdiction or '')}{date_str}{sim_str}<br/>"
        f'<span style="color:var(--text-secondary);font-size:0.82rem">{escape(arts_str)}</span>'
        f"</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_stat_card(label: str, value: str, color: str | None = None) -> None:
    """Render a metric card with label and big number."""
    c = f'color:{color};' if color else ""
    html = (
        f'<div class="jm-label">{escape(label)}</div>'
        f'<div class="jm-big-number" style="{c}">{escape(value)}</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_factor_tag(
    factor: str,
    direction: str,
    pct: float | None = None,
    cases: int | None = None,
) -> str:
    """Return HTML for a factor impact tag. Call st.markdown on the result."""
    css = "jm-factor-mitigating" if direction == "mitigating" else "jm-factor-aggravating"
    sign = "-" if direction == "mitigating" else "+"
    pct_str = f" {sign}{abs(pct):.0f}%" if pct is not None else ""
    cases_str = f" ({cases} cases)" if cases else ""
    return (
        f'<span class="jm-factor {css}">'
        f"{escape(factor)}{pct_str}{cases_str}"
        f"</span>"
    )


def render_dpa_comparison_strip(
    comparisons: dict[str, dict],
) -> None:
    """Render horizontal comparison bars for DPA median fines."""
    if not comparisons:
        return
    max_fine = max(v["median_fine"] for v in comparisons.values()) or 1

    st.markdown("##### How other DPAs handle similar violations")
    for dpa, data in list(comparisons.items())[:8]:
        med = data["median_fine"]
        cases = data.get("cases", 0)
        width_pct = max(3, med / max_fine * 100)
        html = (
            f'<div style="margin-bottom:6px;">'
            f'<span style="font-size:0.82rem;display:inline-block;width:120px">'
            f"{escape(dpa)}</span>"
            f'<span class="jm-number" style="font-size:0.82rem;width:90px;display:inline-block">'
            f"{format_eur(med)}</span>"
            f'<span style="display:inline-block;width:50%;vertical-align:middle">'
            f'<div class="jm-comp-bar" style="width:{width_pct}%"></div>'
            f"</span>"
            f'<span style="font-size:0.72rem;color:var(--text-secondary);margin-left:8px">'
            f"({cases} cases)</span>"
            f"</div>"
        )
        st.markdown(html, unsafe_allow_html=True)
