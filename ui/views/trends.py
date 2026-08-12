"""Tab 5: Enforcement Trends — time-series analysis of GDPR enforcement."""

import streamlit as st
import psycopg
import pandas as pd

from ui.views.analyzer import JURISDICTIONS, SECTORS, GDPR_ARTICLES

_ARTICLES_SHORT = [
    "5", "6", "9", "12", "13", "14", "15", "17",
    "25", "28", "30", "32", "33", "34", "35",
]


def _build_trend_filters(
    jurisdictions: list[str],
    articles: list[str],
    sectors: list[str],
) -> tuple[str, list]:
    """Build parameterized WHERE clauses for trend queries."""
    clauses: list[str] = []
    params: list = []
    if jurisdictions:
        clauses.append("AND jurisdiction = ANY(%s)")
        params.append(jurisdictions)
    if articles:
        for art in articles:
            clauses.append("AND array_to_string(gdpr_articles, '||') LIKE %s")
            params.append(f"%Art%{art}%")
    if sectors:
        clauses.append("AND sector = ANY(%s)")
        params.append(sectors)
    return " ".join(clauses), params


def render(conn: psycopg.Connection) -> None:
    """Render the Trends tab."""
    st.markdown("### Enforcement Trends")

    # ---- Filters ----
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        jurisdiction_filter = st.multiselect("Jurisdiction", JURISDICTIONS, key="trend_juris")
    with col_f2:
        article_filter = st.multiselect("GDPR Article", _ARTICLES_SHORT, key="trend_art")
    with col_f3:
        sector_filter = st.multiselect("Sector", SECTORS, key="trend_sector")

    filter_sql, filter_params = _build_trend_filters(
        jurisdiction_filter, article_filter, sector_filter,
    )

    active_labels: list[str] = []
    if jurisdiction_filter:
        active_labels.extend(jurisdiction_filter)
    if article_filter:
        active_labels.extend(f"Art. {a}" for a in article_filter)
    if sector_filter:
        active_labels.extend(sector_filter)
    if active_labels:
        st.caption(f"Filtered by: {', '.join(active_labels)}")

    cur = conn.cursor()

    # ---- Overall trends ----
    query = (
        "SELECT decision_year, count(*) as cases, "
        "sum(fine_amount) as total_fines, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY fine_amount) as median "
        "FROM documents "
        "WHERE fine_amount > 0 AND decision_year >= 2018 AND decision_year IS NOT NULL "
        f"{filter_sql} "
        "GROUP BY decision_year ORDER BY decision_year"
    )
    cur.execute(query, filter_params)
    rows = cur.fetchall()

    if not rows:
        st.info("No data for selected filters.")
        return

    df = pd.DataFrame(rows, columns=["Year", "Cases", "Total Fines", "Median Fine"])
    df["Year"] = df["Year"].astype(int)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### Cases per Year")
        st.bar_chart(df.set_index("Year")["Cases"])
    with col2:
        st.markdown("##### Total Fines per Year")
        st.bar_chart(df.set_index("Year")["Total Fines"])

    st.markdown("##### Median Fine per Year")
    st.line_chart(df.set_index("Year")["Median Fine"])

    # ---- Top articles over time ----
    _render_article_trends(cur, filter_sql, filter_params)


def _render_article_trends(
    cur: psycopg.Cursor,
    filter_sql: str,
    filter_params: list,
) -> None:
    """Top 5 articles — cases per year line chart."""
    query_top = (
        "SELECT unnest(gdpr_articles) as art, count(*) as n "
        "FROM documents WHERE fine_amount > 0 "
        f"{filter_sql} "
        "GROUP BY art ORDER BY n DESC LIMIT 5"
    )
    cur.execute(query_top, filter_params)
    top_articles = [r[0] for r in cur.fetchall()]

    if not top_articles:
        return

    st.markdown("##### Top 5 Articles — Cases per Year")
    art_data = []
    for art in top_articles:
        query_art = (
            "SELECT decision_year, count(*) "
            "FROM documents "
            "WHERE fine_amount > 0 "
            "AND decision_year >= 2018 "
            "AND decision_year IS NOT NULL "
            "AND %s = ANY(gdpr_articles) "
            f"{filter_sql} "
            "GROUP BY decision_year ORDER BY decision_year"
        )
        cur.execute(query_art, [art] + filter_params)
        for yr, cnt in cur.fetchall():
            art_data.append({"Year": int(yr), "Article": art, "Cases": cnt})

    if art_data:
        art_df = pd.DataFrame(art_data)
        pivot = art_df.pivot(index="Year", columns="Article", values="Cases").fillna(0)
        st.line_chart(pivot)
