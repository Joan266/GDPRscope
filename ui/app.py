"""
JurisMind — GDPR Enforcement Analyzer
Streamlit entry point: page config, header, stats, tab routing.

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 streamlit run ui/app.py --server.port 8501
"""

import os
import sys
from pathlib import Path

import psycopg
import streamlit as st

# Project root on sys.path so `from services...` works
sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.styles import inject_css
from ui.views import analyzer, my_dpa, search, compare, trends, case_detail

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ---- Page config ----
st.set_page_config(
    page_title="JurisMind — GDPR Enforcement Analyzer",
    page_icon="&#9878;",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(inject_css(), unsafe_allow_html=True)


# ---- DB connection ----
@st.cache_resource
def get_conn() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, autocommit=True)


def get_db_stats(conn: psycopg.Connection) -> tuple[int, int, int]:
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM documents")
    docs = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM case_factors")
    factors = cur.fetchone()[0]
    cur.execute(
        "SELECT count(DISTINCT jurisdiction) FROM documents "
        "WHERE jurisdiction IS NOT NULL"
    )
    jurisdictions = cur.fetchone()[0]
    return docs, factors, jurisdictions


# ---- Main ----
def main() -> None:
    if not DATABASE_URL:
        st.error(
            "DATABASE_URL not set. "
            "Run: export $(grep -v '^#' .env | xargs)"
        )
        return

    # Header
    st.markdown("# JurisMind")
    st.markdown("GDPR Enforcement Analyzer with Persistent Memory")

    # Stats bar
    try:
        conn = get_conn()
        docs, factors, jurisdictions = get_db_stats(conn)
        c1, c2, c3 = st.columns(3)
        c1.metric("Enforcement Decisions", f"{docs:,}")
        c2.metric("Extracted Factors", f"{factors:,}")
        c3.metric("Jurisdictions", f"{jurisdictions}")
    except Exception as e:
        st.warning(f"DB connection issue: {e}")
        return

    # Disclaimer
    st.caption(
        "Based on real enforcement decisions — not theoretical maximums. "
        "Not legal advice."
    )

    # Tabs
    tabs = st.tabs([
        "Analyzer", "My DPA", "Search", "Compare", "Trends", "Case Detail",
    ])

    with tabs[0]:
        analyzer.render(conn)
    with tabs[1]:
        my_dpa.render(conn)
    with tabs[2]:
        search.render(conn)
    with tabs[3]:
        compare.render(conn)
    with tabs[4]:
        trends.render(conn)
    with tabs[5]:
        case_detail.render(conn)


if __name__ == "__main__":
    main()
