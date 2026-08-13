"""
GDPRScope — Enforcement Intelligence Platform
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
from ui.views import intelligence, analyzer, my_dpa, search, compare, trends, case_detail, research

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ---- Page config ----
st.set_page_config(
    page_title="GDPRScope — Enforcement Intelligence",
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

    # Header — compact bar with inline stats
    try:
        conn = get_conn()
        docs, factors, jurisdictions = get_db_stats(conn)
    except Exception as e:
        st.warning(f"DB connection issue: {e}")
        return

    st.markdown(
        f'<div class="gs-header">'
        f'<div class="gs-brand">'
        f'<span class="gs-logo">GDPRScope</span>'
        f'<span class="gs-subtitle">Enforcement Intelligence Platform</span>'
        f'</div>'
        f'<div class="gs-stats">'
        f'<span class="gs-stat"><strong>{docs:,}</strong> decisions</span>'
        f'<span class="gs-stat-sep">·</span>'
        f'<span class="gs-stat"><strong>{factors:,}</strong> factors</span>'
        f'<span class="gs-stat-sep">·</span>'
        f'<span class="gs-stat"><strong>{jurisdictions}</strong> jurisdictions</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Tabs
    tabs = st.tabs([
        "Research", "Intelligence", "Analyzer", "My DPA", "Search",
        "Compare", "Trends", "Case Detail",
    ])

    with tabs[0]:
        research.render(conn)
    with tabs[1]:
        intelligence.render(conn)
    with tabs[2]:
        analyzer.render(conn)
    with tabs[3]:
        my_dpa.render(conn)
    with tabs[4]:
        search.render(conn)
    with tabs[5]:
        compare.render(conn)
    with tabs[6]:
        trends.render(conn)
    with tabs[7]:
        case_detail.render(conn)


if __name__ == "__main__":
    main()
