"""Tab 6: Case Detail — single case deep-dive."""

import json

import streamlit as st
import psycopg

from ui.components.cards import render_stat_card
from ui.components.formatting import escape, format_eur, severity_color


def render(conn: psycopg.Connection) -> None:
    """Render the Case Detail tab."""
    st.markdown("### Case Detail")

    query = st.text_input(
        "Search case by title", placeholder="e.g. AEPD, CNIL, Meta",
    )
    if not query:
        st.info("Enter a case name to search.")
        return

    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, jurisdiction, fine_amount
        FROM documents
        WHERE title ILIKE %s
        ORDER BY fine_amount DESC
        LIMIT 20
    """, (f"%{query}%",))
    results = cur.fetchall()

    if not results:
        st.warning("No cases found matching your search.")
        return

    case_options = {
        (f"{r[1]} — {format_eur(r[3])}" if r[3] else r[1]): r[0]
        for r in results
    }
    selected = st.selectbox("Select case", list(case_options.keys()))
    doc_id = case_options[selected]

    _render_case(cur, doc_id)


def _render_case(cur: psycopg.Cursor, doc_id: str) -> None:
    """Fetch and display full case details."""
    cur.execute("""
        SELECT title, jurisdiction, authority, fine_amount, decision_date,
               gdpr_articles, sector, outcome, summary_facts, summary_holding,
               source_urls, controller_name, violation_type
        FROM documents WHERE id = %s
    """, (doc_id,))
    case = cur.fetchone()
    if not case:
        return

    (title, juris, authority, fine, date, arts, sector, outcome,
     facts, holding, urls, controller, vtype) = case

    # ---- Header ----
    col1, col2 = st.columns([6, 4])
    with col1:
        st.markdown(f"## {escape(title)}")
        st.markdown(f"**Authority:** {escape(authority or juris or 'N/A')}")
        st.markdown(
            f"**Date:** {escape(str(date) if date else 'N/A')} · "
            f"**Sector:** {escape(sector or 'N/A')}"
        )
        st.markdown(
            f"**Outcome:** {escape(outcome or 'N/A')} · "
            f"**Violation type:** {escape(vtype or 'N/A')}"
        )
        if controller:
            st.markdown(f"**Controller:** {escape(controller)}")
        if arts:
            st.markdown(f"**Articles:** {escape(', '.join(arts))}")

    with col2:
        if fine:
            render_stat_card("Fine", format_eur(fine), severity_color(fine))

    # ---- Content ----
    if facts:
        with st.expander("Facts", expanded=True):
            st.write(facts)
    if holding:
        with st.expander("Holding / Decision", expanded=True):
            st.write(holding)

    # ---- Case factors ----
    _render_factors(cur, doc_id)

    # ---- Source URLs ----
    if urls:
        st.markdown("##### Source URLs")
        url_list = urls if isinstance(urls, list) else [urls]
        for u in url_list:
            if isinstance(u, str):
                st.markdown(f"- [{escape(u)}]({u})")


def _render_factors(cur: psycopg.Cursor, doc_id: str) -> None:
    """Show extracted Art. 83(2) factors if available."""
    cur.execute("""
        SELECT factor_a_gravity, factor_b_intent, factor_c_mitigation,
               factor_f_cooperation, factor_g_data_categories,
               overall_assessment
        FROM case_factors WHERE document_id = %s
    """, (doc_id,))
    factors = cur.fetchone()
    if not factors:
        return

    st.markdown("##### Extracted Art. 83(2) Factors")
    labels = [
        "Gravity", "Intent", "Mitigation",
        "Cooperation", "Data Categories", "Assessment",
    ]
    for label, val in zip(labels, factors):
        if not val:
            continue
        if isinstance(val, dict):
            st.markdown(f"**{label}:** {json.dumps(val, ensure_ascii=False)}")
        else:
            st.markdown(f"**{label}:** {escape(str(val))}")
