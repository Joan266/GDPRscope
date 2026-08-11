"""Tab 5: Enforcement Trends — time-series analysis of GDPR enforcement."""

import streamlit as st
import psycopg
import pandas as pd


def render(conn: psycopg.Connection) -> None:
    """Render the Trends tab."""
    st.markdown("### Enforcement Trends")

    cur = conn.cursor()

    # ---- Overall trends ----
    cur.execute("""
        SELECT decision_year,
               count(*) as cases,
               sum(fine_amount) as total_fines,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fine_amount) as median
        FROM documents
        WHERE fine_amount > 0 AND decision_year >= 2018 AND decision_year IS NOT NULL
        GROUP BY decision_year
        ORDER BY decision_year
    """)
    rows = cur.fetchall()

    if not rows:
        st.warning("No trend data available.")
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
    _render_article_trends(cur)


def _render_article_trends(cur: psycopg.Cursor) -> None:
    """Top 5 articles — cases per year line chart."""
    cur.execute("""
        SELECT unnest(gdpr_articles) as art, count(*) as n
        FROM documents WHERE fine_amount > 0
        GROUP BY art ORDER BY n DESC LIMIT 5
    """)
    top_articles = [r[0] for r in cur.fetchall()]

    if not top_articles:
        return

    st.markdown("##### Top 5 Articles — Cases per Year")
    art_data = []
    for art in top_articles:
        cur.execute("""
            SELECT decision_year, count(*)
            FROM documents
            WHERE fine_amount > 0
              AND decision_year >= 2018
              AND decision_year IS NOT NULL
              AND %s = ANY(gdpr_articles)
            GROUP BY decision_year
            ORDER BY decision_year
        """, (art,))
        for yr, cnt in cur.fetchall():
            art_data.append({"Year": int(yr), "Article": art, "Cases": cnt})

    if art_data:
        art_df = pd.DataFrame(art_data)
        pivot = art_df.pivot(index="Year", columns="Article", values="Cases").fillna(0)
        st.line_chart(pivot)
