"""Tab 3: Case Search — filter and browse enforcement decisions."""

import streamlit as st
import psycopg

from ui.components.cards import render_precedent_card
from ui.components.formatting import escape

_JURISDICTIONS = [
    "Spain", "France", "Italy", "Germany", "Ireland", "Netherlands",
    "Austria", "Belgium", "Sweden", "Norway", "Finland", "Denmark",
    "Poland", "Romania", "Greece", "Portugal", "Czech Republic",
    "Hungary", "Croatia", "Bulgaria",
]

_ARTICLES_SHORT = [
    "5", "6", "7", "9", "12", "13", "14", "15", "17", "21",
    "25", "28", "30", "32", "33", "34", "35", "44", "46",
]


def render(conn: psycopg.Connection) -> None:
    """Render the Search tab."""
    st.markdown("### Case Search")

    col_filters, col_results = st.columns([3, 7])

    with col_filters:
        query = st.text_input("Search", placeholder="e.g. data breach notification")
        jurisdiction_filter = st.multiselect("Jurisdiction", _JURISDICTIONS)
        article_filter = st.multiselect("GDPR Articles", _ARTICLES_SHORT)
        fine_range = st.slider(
            "Fine range (EUR)", 0, 1_000_000, (0, 1_000_000), step=10_000,
            format="EUR %d",
        )
        sort_by = st.selectbox("Sort by", [
            "Fine (high to low)", "Fine (low to high)",
            "Date (newest)", "Date (oldest)",
        ])
        search = st.button("Search", type="primary", use_container_width=True)

    with col_results:
        if not (search or query):
            st.info("Enter a search term or adjust filters, then click Search.")
            return

        rows = _execute_search(
            conn, query, jurisdiction_filter, article_filter, fine_range, sort_by,
        )
        st.caption(f"{len(rows)} results")

        for r in rows:
            title, juris, fine, arts, sector, date_val, teaser = (
                r[0], r[1], r[2], r[3], r[4], r[5], r[6],
            )
            render_precedent_card(
                title=title,
                jurisdiction=juris,
                fine_amount=fine,
                articles=arts,
                date=str(date_val) if date_val else None,
            )
            if teaser:
                st.caption(escape(teaser[:200]))


def _execute_search(
    conn: psycopg.Connection,
    query: str,
    jurisdictions: list[str],
    articles: list[str],
    fine_range: tuple[int, int],
    sort_by: str,
) -> list[tuple]:
    """Build and execute parameterized search query."""
    sql = """
        SELECT title, jurisdiction, fine_amount, gdpr_articles,
               sector, decision_date, summary_teaser
        FROM documents
        WHERE fine_amount >= %s AND fine_amount <= %s
    """
    params: list = [fine_range[0], fine_range[1]]

    if query:
        sql += " AND search_vector @@ plainto_tsquery('english', %s)"
        params.append(query)

    if jurisdictions:
        sql += " AND jurisdiction = ANY(%s)"
        params.append(jurisdictions)

    if articles:
        art_conds = " OR ".join(
            "array_to_string(gdpr_articles, '||') LIKE %s" for _ in articles
        )
        sql += f" AND ({art_conds})"
        params.extend(f"%Art%{a}%" for a in articles)

    order_map = {
        "Fine (high to low)": "fine_amount DESC",
        "Fine (low to high)": "fine_amount ASC",
        "Date (newest)": "decision_date DESC NULLS LAST",
        "Date (oldest)": "decision_date ASC NULLS LAST",
    }
    sql += f" ORDER BY {order_map.get(sort_by, 'fine_amount DESC')} LIMIT 50"

    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()
