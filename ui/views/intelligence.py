"""Intelligence Search tab — smart query routing with entity dossiers."""

import streamlit as st
import psycopg

from services.intelligence import (
    AggregationView,
    ArticleView,
    EntityDossier,
    RagView,
    run_intelligence_query,
)
from ui.components.cards import render_precedent_card, render_stat_card
from ui.components.formatting import escape, format_eur


def render(conn: psycopg.Connection) -> None:
    """Render the Intelligence Search tab."""
    st.markdown("### Intelligence Search")

    query = st.text_input(
        "Query",
        placeholder="e.g. Vodafone, Article 32 violations, largest fines 2024...",
        key="intelligence_query",
    )

    if not query or not query.strip():
        st.info(
            "Type a natural language query. Examples:\n"
            "- **Meta** — entity dossier with all cases and stats\n"
            "- **Article 32 violations** — cases citing a specific article\n"
            "- **Largest fines in Spain** — aggregation by fine amount\n"
            "- **Legitimate interest for marketing** — semantic search"
        )
        return

    with st.spinner("Routing query..."):
        result = run_intelligence_query(conn, query.strip())

    # Route badge
    route = result.route_info.route
    badge_css = {
        "entity": "jm-badge-green",
        "article": "jm-badge-amber",
        "aggregation": "jm-badge-amber",
        "rag": "jm-badge-red",
    }
    badge_label = {
        "entity": f"Entity Dossier \u00b7 {escape(result.route_info.matched_term)}",
        "article": f"Article View \u00b7 {escape(result.route_info.matched_term)}",
        "aggregation": f"Aggregation \u00b7 {escape(result.route_info.matched_term)}",
        "rag": "Semantic Search",
    }
    st.markdown(
        f'<span class="jm-badge {badge_css.get(route, "")}">'
        f'{badge_label.get(route, route)}</span>',
        unsafe_allow_html=True,
    )

    if isinstance(result, EntityDossier):
        _render_entity_dossier(result)
    elif isinstance(result, ArticleView):
        _render_article_view(result)
    elif isinstance(result, AggregationView):
        _render_aggregation_view(result)
    elif isinstance(result, RagView):
        _render_rag_view(result)


# ---- Entity Dossier ----

def _render_entity_dossier(d: EntityDossier) -> None:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_stat_card("Total Cases", str(d.total_cases))
    with c2:
        render_stat_card("Total Fines", format_eur(d.total_fines))
    with c3:
        render_stat_card("Median Fine", format_eur(d.median_fine))
    with c4:
        render_stat_card("Max Fine", format_eur(d.max_fine), color="#DC2626")

    col_trend, col_articles = st.columns(2)

    with col_trend:
        if d.yearly_trend:
            st.markdown("##### Yearly Trend")
            import altair as alt
            import pandas as pd
            df = pd.DataFrame(d.yearly_trend)
            chart = (
                alt.Chart(df)
                .mark_bar(color="#2563EB")
                .encode(
                    x=alt.X("year:O", title="Year"),
                    y=alt.Y("count:Q", title="Cases"),
                    tooltip=["year", "count", "total_fines"],
                )
                .properties(height=220)
            )
            st.altair_chart(chart, use_container_width=True)

    with col_articles:
        if d.top_articles:
            st.markdown("##### Top Articles Violated")
            for a in d.top_articles[:8]:
                st.markdown(
                    f"- **{escape(a['article'])}** ({a['count']} cases)"
                )

    if d.jurisdictions:
        st.markdown("##### Cross-Jurisdiction Enforcement")
        for j in d.jurisdictions:
            st.markdown(
                f"- **{escape(j['jurisdiction'])}**: "
                f"{j['count']} cases, {format_eur(j['total_fines'])}"
            )

    _render_cases(d.cases, "Enforcement Decisions")


# ---- Article View ----

def _render_article_view(a: ArticleView) -> None:
    c1, c2, c3 = st.columns(3)
    with c1:
        render_stat_card("Cases", str(a.total_cases))
    with c2:
        render_stat_card("Total Fines", format_eur(a.total_fines))
    with c3:
        render_stat_card("Median Fine", format_eur(a.median_fine))

    col_dpas, col_sectors = st.columns(2)

    with col_dpas:
        if a.top_dpas:
            st.markdown("##### Top DPAs")
            for d in a.top_dpas[:8]:
                st.markdown(
                    f"- **{escape(d['jurisdiction'])}**: "
                    f"{d['count']} cases, {format_eur(d['total_fines'])}"
                )

    with col_sectors:
        if a.top_sectors:
            st.markdown("##### Top Sectors")
            for s in a.top_sectors[:8]:
                st.markdown(f"- **{escape(s['sector'])}** ({s['count']} cases)")

    _render_cases(a.cases, f"{a.article_label} Decisions")


# ---- Aggregation View ----

def _render_aggregation_view(agg: AggregationView) -> None:
    c1, c2 = st.columns(2)
    with c1:
        render_stat_card("Total Results", f"{agg.total_results:,}")
    with c2:
        render_stat_card("Total Fines", format_eur(agg.total_fines))

    if agg.filters_applied:
        parts = [f"**{k}**: {v}" for k, v in agg.filters_applied.items()]
        st.caption("Filters: " + " | ".join(parts))

    _render_cases(agg.cases, "Results")


# ---- RAG View ----

def _render_rag_view(rag: RagView) -> None:
    if not rag.results:
        st.info("No documents matched your query. Try the Search tab for filter-based browsing.")
        return

    st.caption(f"{rag.total_results} documents found via text search")

    for r in rag.results[:30]:
        render_precedent_card(
            title=r["title"],
            jurisdiction=r.get("jurisdiction"),
            fine_amount=r.get("fine_amount"),
            articles=r.get("gdpr_articles"),
            date=r.get("decision_date"),
        )
        teaser = r.get("summary_teaser")
        if teaser:
            st.caption(escape(teaser[:200]))


# ---- Shared ----

def _render_cases(cases: list, heading: str) -> None:
    if not cases:
        st.info("No cases found for this query.")
        return

    st.markdown(f"##### {heading} ({len(cases)})")
    for c in cases[:30]:
        render_precedent_card(
            title=c.title,
            jurisdiction=c.jurisdiction,
            fine_amount=c.fine_amount,
            articles=c.gdpr_articles,
            date=c.decision_date,
        )
        if c.summary_teaser:
            st.caption(escape(c.summary_teaser[:200]))
