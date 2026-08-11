"""Tab 1: Enforcement Analyzer — main page.

Form inputs + URL auto-profile + range bar + precedents + DPA comparison.
"""

import streamlit as st
import psycopg

from services.fine_simulator import SimulationInput, simulate_fine
from services.profile_scraper import scrape_privacy_policy, extract_org_profile
from ui.components.cards import (
    render_dpa_comparison_strip,
    render_factor_tag,
    render_precedent_card,
    render_stat_card,
)
from ui.components.confidence import render_confidence_badge
from ui.components.range_bar import render_range_bar
from ui.components.formatting import format_eur

JURISDICTIONS = [
    "Spain", "France", "Italy", "Germany", "Ireland", "Netherlands",
    "Austria", "Belgium", "Sweden", "Norway", "Finland", "Denmark",
    "Poland", "Romania", "Greece", "Portugal", "Czech Republic",
    "Hungary", "Croatia", "Bulgaria", "Cyprus", "Estonia", "Latvia",
    "Lithuania", "Luxembourg", "Malta", "Slovakia", "Slovenia",
    "United Kingdom", "Iceland", "Liechtenstein",
]

SECTORS = [
    "Private/Limited Company", "Public body", "Telecommunications",
    "Health care", "Finance/Insurance", "Transportation",
    "Employment", "Media", "Real estate", "Education",
    "Hospitality/Tourism", "Energy", "Industry and Commerce",
]

GDPR_ARTICLES = [
    "5", "5(1)(a)", "5(1)(b)", "5(1)(c)", "5(1)(d)", "5(1)(e)", "5(1)(f)", "5(2)",
    "6", "6(1)", "6(1)(a)", "6(1)(b)", "6(1)(c)", "6(1)(f)",
    "7", "9", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22",
    "24", "25", "26", "27", "28", "30", "31", "32", "33", "34", "35", "36",
    "37", "44", "45", "46", "58",
]

DATA_CATEGORIES = [
    ("general", "General personal data"),
    ("health", "Health / medical data"),
    ("financial", "Financial data"),
    ("biometric", "Biometric data"),
    ("genetic", "Genetic data"),
    ("children", "Children's data"),
    ("location", "Location data"),
    ("criminal", "Criminal records"),
]


def render(conn: psycopg.Connection) -> None:
    """Render the Enforcement Analyzer tab."""
    st.markdown("### Enforcement Analyzer")
    st.markdown(
        "Estimate your GDPR exposure range using the **EDPB 5-step methodology** "
        "against real enforcement decisions."
    )

    # ---- URL Auto-Profile ----
    _render_url_section()

    col_form, col_spacer, col_result = st.columns([4, 0.5, 6])

    with col_form:
        params = _render_form()

    with col_result:
        if params is not None:
            _render_results(conn, params)
        else:
            _render_placeholder()


def _render_url_section() -> None:
    """URL input for auto-profiling from privacy policy."""
    with st.expander("Smart Analysis — paste your company URL", expanded=False):
        url = st.text_input(
            "Company URL",
            placeholder="e.g. https://stripe.com",
            label_visibility="collapsed",
        )
        if st.button("Scan Privacy Policy", disabled=not url):
            with st.spinner("Scanning privacy policy..."):
                text = scrape_privacy_policy(url)
                if not text:
                    st.error("Could not find a privacy policy at that URL.")
                    return
                profile = extract_org_profile(text, url)
                if profile:
                    st.session_state["org_profile"] = profile
                    st.success(
                        f"Profile detected: **{profile.company_name or url}** "
                        f"· {profile.jurisdiction or 'Unknown'} "
                        f"· {profile.sector or 'Unknown'}"
                    )
                else:
                    st.warning("Could not extract profile. Fill the form manually.")


def _render_form() -> SimulationInput | None:
    """Render the violation profile form. Returns SimulationInput on submit."""
    st.markdown("##### Violation Profile")

    profile = st.session_state.get("org_profile")
    default_juris = ""
    default_sector = ""
    if profile:
        default_juris = profile.jurisdiction or ""
        default_sector = profile.sector or ""

    juris_options = [""] + JURISDICTIONS
    juris_idx = juris_options.index(default_juris) if default_juris in juris_options else 0
    jurisdiction = st.selectbox("Jurisdiction", juris_options, index=juris_idx)

    sector_options = [""] + SECTORS
    sector_idx = 0
    for i, s in enumerate(sector_options):
        if default_sector and default_sector.lower() in s.lower():
            sector_idx = i
            break
    sector = st.selectbox("Sector", sector_options, index=sector_idx)

    articles = st.multiselect(
        "GDPR Articles Violated", GDPR_ARTICLES, default=["32"],
    )

    st.markdown("##### Financial & Scale")
    turnover = st.number_input(
        "Annual turnover (EUR)", min_value=0, value=0, step=100_000,
    )
    data_subjects = st.number_input(
        "Data subjects affected", min_value=0, value=0, step=100,
    )
    data_cat = st.selectbox(
        "Data categories",
        [dc[0] for dc in DATA_CATEGORIES],
        format_func=lambda x: dict(DATA_CATEGORIES)[x],
    )

    st.markdown("##### Aggravating / Mitigating Factors")
    col_a, col_b = st.columns(2)
    with col_a:
        intentional = st.checkbox("Intentional violation")
        prior_violations = st.checkbox("Prior violations")
    with col_b:
        cooperated = st.checkbox("Cooperated with DPA", value=True)
        notified = st.checkbox("Voluntarily notified", value=True)
        corrective = st.checkbox("Took corrective measures", value=True)
        prior_security = st.checkbox("Had prior security measures", value=True)

    run = st.button("Analyze Exposure", type="primary", use_container_width=True)

    if not run:
        return None
    if not articles:
        st.warning("Please select at least one GDPR article.")
        return None

    return SimulationInput(
        articles_violated=articles,
        jurisdiction=jurisdiction or None,
        sector=sector or None,
        turnover_eur=turnover if turnover > 0 else None,
        data_subjects_affected=data_subjects if data_subjects > 0 else None,
        data_categories=data_cat,
        intentional=intentional,
        prior_violations=prior_violations,
        cooperated=cooperated,
        notified_voluntarily=notified,
        corrective_measures=corrective,
        prior_security_measures=prior_security,
    )


def _render_results(conn: psycopg.Connection, params: SimulationInput) -> None:
    """Run simulation and render results."""
    result = simulate_fine(conn, params)
    r = result.estimated_range

    # ---- Exposure Range ----
    st.markdown("##### Exposure Range")
    render_range_bar(
        p25=r.get("percentile_25", 0),
        median=r.get("median", 0),
        p75=r.get("percentile_75", 0),
        min_val=r.get("min", 0),
        max_val=r.get("max", 0),
        precedent_count=r.get("precedent_count", 0),
    )

    # ---- Confidence ----
    if result.confidence:
        render_confidence_badge(result.confidence)

    if r.get("note"):
        st.info(r["note"])

    # ---- Key numbers ----
    cols = st.columns(3)
    with cols[0]:
        render_stat_card("P25", format_eur(r.get("percentile_25")), "#059669")
    with cols[1]:
        render_stat_card("Median", format_eur(r.get("median")), "#1B2A4A")
    with cols[2]:
        render_stat_card("P75", format_eur(r.get("percentile_75")), "#D97706")

    # ---- Factor Impacts ----
    if result.factor_impacts:
        st.markdown("##### Factor Impacts")
        tags = "".join(
            render_factor_tag(
                f.factor, f.direction, f.avg_reduction_pct, f.cases_count,
            )
            for f in result.factor_impacts
        )
        st.markdown(tags, unsafe_allow_html=True)

    # ---- Methodology ----
    meth = result.methodology
    with st.expander("EDPB 5-Step Methodology", expanded=False):
        st.markdown(f"**Step 1 — Categorize:** {meth['step1_category']}")
        sp = meth["step2_starting_point"]
        st.markdown(
            f"**Step 2 — Starting point:** {sp['range']} "
            f"(legal max: EUR {sp['legal_max']:,})"
        )
        factors = meth["step3_factors"]
        if factors["aggravating"]:
            st.markdown("**Step 3a — Aggravating:** " + ", ".join(factors["aggravating"]))
        if factors["mitigating"]:
            st.markdown("**Step 3b — Mitigating:** " + ", ".join(factors["mitigating"]))
        st.markdown(f"**Step 4 — Legal max:** {meth['step4_legal_max']}")
        st.markdown(f"**Step 5 — Precedent range:** {meth['step5_precedent_range']}")

    # ---- Precedents ----
    if result.precedents:
        st.markdown("##### Top Precedent Cases")
        for p in result.precedents[:5]:
            render_precedent_card(
                title=p.title,
                jurisdiction=p.jurisdiction,
                fine_amount=p.fine_amount,
                articles=p.articles,
                similarity=p.similarity_score,
            )

    # ---- DPA Comparison ----
    if result.dpa_comparison:
        render_dpa_comparison_strip(result.dpa_comparison)

    # ---- Disclaimer ----
    st.markdown(
        '<div class="jm-disclaimer">'
        "This is a statistical range based on public enforcement data. "
        "It does not constitute legal advice. Actual fines depend on DPA discretion "
        "and case-specific circumstances."
        "</div>",
        unsafe_allow_html=True,
    )


def _render_placeholder() -> None:
    """Placeholder shown before analysis is run."""
    st.markdown(
        '<div style="text-align:center;padding:60px 20px;color:var(--text-secondary)">'
        '<div style="font-size:2.5rem;margin-bottom:12px">&#9878;</div>'
        '<div style="font-size:1.05rem">Configure the violation profile and click '
        "<strong>Analyze Exposure</strong></div>"
        '<div style="font-size:0.85rem;margin-top:8px">'
        "Based on the EDPB 5-step methodology against "
        "real enforcement decisions.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
