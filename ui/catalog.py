"""
JurisMind — Catálogo de jurisprudencia GDPR + Motor de investigación RAG
Streamlit app para visualizar documentos en CockroachDB.

Uso:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 streamlit run ui/catalog.py
"""

import json
import os
import sys
import time
from pathlib import Path

import psycopg
import streamlit as st

# Añadir raíz del proyecto a sys.path para importar db/rag.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import rag as rag_module

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Página ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="JurisMind — GDPR Case Catalog",
    page_icon="⚖",
    layout="wide",
)

# ── DB ──────────────────────────────────────────────────────────────────────────

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


@st.cache_resource
def get_bedrock_client():
    return rag_module.make_bedrock_client()


@st.cache_resource
def get_conn():
    if not DATABASE_URL:
        st.error("DATABASE_URL no definida. Exporta la variable de entorno y relanza.")
        st.stop()
    return psycopg.connect(DATABASE_URL, autocommit=True)


@st.cache_data(ttl=60)
def load_stats():
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT source, COUNT(*) FROM documents GROUP BY source ORDER BY source
        """)
        by_source = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM documents")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*), COUNT(embedding) FROM chunks")
        chunks_total, chunks_emb = cur.fetchone()
    return total, by_source, chunks_total, chunks_emb


@st.cache_data(ttl=30)
def load_documents(source_filter, authority_filter, year_from, year_to, has_fine, search_text, limit, offset):
    conditions = ["1=1"]
    filter_params: list = []  # params for WHERE clause only

    if source_filter != "All":
        conditions.append("source = %s")
        filter_params.append(source_filter)
    if authority_filter:
        conditions.append("authority_abbrev ILIKE %s")
        filter_params.append(f"%{authority_filter}%")
    if year_from:
        conditions.append("decision_year >= %s")
        filter_params.append(year_from)
    if year_to:
        conditions.append("decision_year <= %s")
        filter_params.append(year_to)
    if has_fine:
        conditions.append("fine_amount IS NOT NULL")
    if search_text:
        conditions.append(
            "(title ILIKE %s OR summary_teaser ILIKE %s OR controller_name ILIKE %s)"
        )
        like = f"%{search_text}%"
        filter_params.extend([like, like, like])

    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, source, title, jurisdiction, authority_abbrev,
               decision_year, fine_amount,
               array_to_string(gdpr_articles, ', ') AS articles,
               outcome, source_urls
        FROM documents
        WHERE {where}
        ORDER BY decision_year DESC NULLS LAST, ingested_at DESC
        LIMIT %s OFFSET %s
    """

    with get_conn().cursor() as cur:
        cur.execute(sql, filter_params + [limit, offset])
        rows = cur.fetchall()
        # COUNT uses only filter_params — pagination params (limit/offset) are separate.
        cur.execute(f"SELECT COUNT(*) FROM documents WHERE {where}", filter_params)
        total = cur.fetchone()[0]

    return rows, total


@st.cache_data(ttl=60)
def load_document_detail(doc_id: str):
    with get_conn().cursor() as cur:
        cur.execute("""
            SELECT id, source, source_id, title, document_type,
                   jurisdiction, authority, authority_abbrev,
                   case_number, ecli, celex,
                   decision_date, decision_year,
                   case_type, outcome,
                   fine_amount, fine_currency, sector, controller_name,
                   gdpr_articles, parties, source_urls, national_laws,
                   summary_teaser, summary_facts, summary_dispute, summary_holding,
                   ingested_at
            FROM documents WHERE id = %s
        """, [doc_id])
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


# ── Helpers ──────────────────────────────────────────────────────────────────────

def fmt_eur(amount: int | None) -> str:
    if amount is None:
        return "—"
    if amount >= 1_000_000:
        return f"€{amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"€{amount/1_000:.0f}K"
    return f"€{amount}"


def source_badge(source: str) -> str:
    import html as _html
    colors = {
        "gdprhub": "#1a6b5c",
        "enforcement_tracker": "#7b3a10",
        "eurlex": "#1a3a6b",
    }
    labels = {"gdprhub": "GDPRhub", "enforcement_tracker": "Tracker", "eurlex": "EUR-Lex"}
    color = colors.get(source, "#444")
    # Escape fallback to prevent XSS if DB contains an unexpected source value.
    label = labels.get(source) or _html.escape(source or "unknown")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:0.75em">{label}</span>'


def build_source_link(doc: dict) -> tuple[str | None, str | None]:
    """Devuelve el primer URL disponible del documento original."""
    source_urls = doc.get("source_urls")
    if source_urls and isinstance(source_urls, list) and source_urls:
        entry = source_urls[0]
        if isinstance(entry, dict) and entry.get("url"):
            lang = entry.get("language", "")
            name = entry.get("name", "original document")
            return entry["url"], f"{name} ({lang})" if lang else name
    # Fallback EUR-Lex
    if doc.get("celex"):
        url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{doc['celex']}"
        return url, "EUR-Lex (EN)"
    return None, None


# ── Header ───────────────────────────────────────────────────────────────────────

st.title("⚖ JurisMind — GDPR Research Agent")

tab_catalog, tab_research = st.tabs(["📚 Case Catalog", "🔍 Research"])

# ── Sidebar (global — visible on both tabs) ───────────────────────────────────────

with st.sidebar:
    st.header("Catalog Filters")
    source_opt = st.selectbox("Source", ["All", "gdprhub", "enforcement_tracker", "eurlex"])
    authority_filter = st.text_input("Authority (abbrev)", placeholder="e.g. AEPD, CNIL, APD/GBA")
    search = st.text_input("Search (title, teaser, controller)", placeholder="e.g. Meta, cookies, CNIL")
    col_y1, col_y2 = st.columns(2)
    year_from = col_y1.number_input("Year from", min_value=2018, max_value=2026, value=2018, step=1)
    year_to = col_y2.number_input("Year to", min_value=2018, max_value=2026, value=2026, step=1)
    only_fines = st.checkbox("Only cases with fine")
    page_size = st.selectbox("Results per page", [20, 50, 100], index=0)
    st.divider()
    st.caption("JurisMind — Hackathon CockroachDB × AWS 2026")

# ── Catalog tab ───────────────────────────────────────────────────────────────────

with tab_catalog:
    try:
        total_docs, by_source, chunks_total, chunks_emb = load_stats()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total documents", f"{total_docs:,}")
        for i, (src, n) in enumerate(by_source):
            label = {"gdprhub": "GDPRhub", "enforcement_tracker": "Tracker", "eurlex": "EUR-Lex"}.get(src, src)
            [col2, col3, col4][i % 3].metric(label, f"{n:,}")
        pct = chunks_emb / chunks_total * 100 if chunks_total else 0
        st.caption(f"Chunks: {chunks_total:,} total — {chunks_emb:,} embedded ({pct:.0f}%)")
    except Exception as e:
        st.error(f"Error conectando a CockroachDB: {e}")
        st.stop()

    st.divider()

    # ── Pagination state ───────────────────────────────────────────────────────────

    if "page" not in st.session_state:
        st.session_state.page = 0

    filter_key = (source_opt, authority_filter, search, year_from, year_to, only_fines, page_size)
    if st.session_state.get("_last_filter") != filter_key:
        st.session_state.page = 0
        st.session_state["_last_filter"] = filter_key

    offset = st.session_state.page * page_size

    rows, total_filtered = load_documents(
        source_filter=source_opt,
        authority_filter=authority_filter,
        year_from=year_from,
        year_to=year_to,
        has_fine=only_fines,
        search_text=search,
        limit=page_size,
        offset=offset,
    )

    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    st.caption(f"{total_filtered:,} results — page {st.session_state.page + 1} of {total_pages}")

    p_col1, p_col2, p_col3 = st.columns([1, 8, 1])
    if p_col1.button("← Prev", disabled=st.session_state.page == 0):
        st.session_state.page -= 1
        st.rerun()
    if p_col3.button("Next →", disabled=st.session_state.page >= total_pages - 1):
        st.session_state.page += 1
        st.rerun()

    st.divider()

    # ── Detail panel ──────────────────────────────────────────────────────────────

    if "selected_doc" not in st.session_state:
        st.session_state.selected_doc = None

    if st.session_state.selected_doc:
        doc = load_document_detail(st.session_state.selected_doc)
        if doc:
            with st.container(border=True):
                close_col, _ = st.columns([1, 9])
                if close_col.button("✕ Close"):
                    st.session_state.selected_doc = None
                    st.rerun()

                st.markdown(f"## {doc['title']}")

                m1, m2, m3, m4, m5 = st.columns(5)
                m1.markdown(f"**Source**<br>{doc['source']}", unsafe_allow_html=True)
                m2.markdown(f"**Jurisdiction**<br>{doc['jurisdiction'] or '—'}", unsafe_allow_html=True)
                m3.markdown(f"**Outcome**<br>{doc['outcome'] or '—'}", unsafe_allow_html=True)
                fine_display = f"{fmt_eur(doc['fine_amount'])} ({doc['fine_amount']:,} {doc.get('fine_currency') or 'EUR'})" if doc['fine_amount'] else "— (no fine)"
                m4.markdown(f"**Fine**<br>{fine_display}", unsafe_allow_html=True)
                m5.markdown(f"**Case type**<br>{doc.get('case_type') or '—'}", unsafe_allow_html=True)

                date_parts = []
                if doc.get("decision_date"):
                    date_parts.append(f"**Decision date:** {doc['decision_date']}")
                if doc.get("date_published"):
                    date_parts.append(f"**Published:** {doc['date_published']}")
                if doc.get("decision_year"):
                    date_parts.append(f"**Year (stored):** {doc['decision_year']}")
                if date_parts:
                    st.markdown(" &nbsp;|&nbsp; ".join(date_parts), unsafe_allow_html=True)

                if doc.get("gdpr_articles"):
                    st.markdown("**GDPR Articles:** " + " · ".join(doc["gdpr_articles"]))
                if doc.get("controller_name"):
                    st.markdown(f"**Controller:** {doc['controller_name']}")
                if doc.get("case_number"):
                    st.markdown(f"**Case number:** {doc['case_number']}")
                if doc.get("authority"):
                    st.markdown(f"**Authority:** {doc['authority']}")

                link_cols = st.columns(3)
                src_url, src_label = build_source_link(doc)
                if src_url:
                    link_cols[0].link_button(f"Original doc — {src_label}", src_url)
                elif doc.get("celex"):
                    link_cols[0].link_button(
                        "EUR-Lex (EN)",
                        f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{doc['celex']}"
                    )
                if doc.get("source") == "gdprhub" and doc.get("source_id"):
                    gdprhub_url = "https://gdprhub.eu/index.php?title=" + doc["source_id"].replace(" ", "_")
                    link_cols[1].link_button("GDPRhub page", gdprhub_url)

                st.divider()

                if doc.get("summary_teaser"):
                    st.markdown("**Summary**")
                    st.info(doc["summary_teaser"])

                tab_labels = []
                tab_contents = []
                for label, field in [("Facts", "summary_facts"), ("Dispute", "summary_dispute"), ("Holding", "summary_holding")]:
                    if doc.get(field):
                        tab_labels.append(label)
                        tab_contents.append(doc[field])

                if tab_labels:
                    inner_tabs = st.tabs(tab_labels)
                    for inner_tab, content in zip(inner_tabs, tab_contents):
                        with inner_tab:
                            st.markdown(content)

            st.divider()

    # ── Rows ──────────────────────────────────────────────────────────────────────

    for row in rows:
        doc_id, source, title, juris, auth, year, fine, articles, outcome, source_urls = row
        doc_id_str = str(doc_id)

        with st.container(border=True):
            r1, r2 = st.columns([9, 1])
            with r1:
                st.markdown(
                    f"{source_badge(source)} &nbsp; **{title}**",
                    unsafe_allow_html=True,
                )
                meta_parts = []
                if juris:
                    meta_parts.append(f"🌍 {juris}")
                if auth:
                    meta_parts.append(f"🏛 {auth}")
                if year:
                    meta_parts.append(f"📅 {year}")
                if fine is not None:
                    meta_parts.append(f"💶 {fmt_eur(fine)}")
                if outcome:
                    meta_parts.append(f"⚖ {outcome}")
                if meta_parts:
                    st.caption(" · ".join(meta_parts))
                if articles:
                    st.caption(f"Articles: {articles[:120]}{'…' if len(articles) > 120 else ''}")
            with r2:
                if st.button("View", key=f"btn_{doc_id_str}"):
                    st.session_state.selected_doc = doc_id_str
                    st.rerun()

    if not rows:
        st.info("No documents match the current filters.")

# ── Research tab ──────────────────────────────────────────────────────────────────

with tab_research:
    st.markdown("### Ask JurisMind")
    st.caption("Consulta en lenguaje natural sobre jurisprudencia GDPR. El motor combina búsqueda semántica y BM25.")

    # ── Query input ───────────────────────────────────────────────────────────────

    query_text = st.text_area(
        "Research question",
        placeholder="e.g. What fine did AEPD impose on Amadeus and why? / ¿Qué multas ha impuesto la AEPD por falta de medidas de seguridad?",
        height=90,
        key="rag_query",
    )

    with st.expander("Advanced filters", expanded=False):
        fc1, fc2, fc3 = st.columns(3)
        rag_jurisdiction = fc1.text_input("Jurisdiction", placeholder="Spain, France, EU…", key="rag_juris")
        rag_source = fc2.selectbox(
            "Source", ["(any)", "gdprhub", "enforcement_tracker", "eurlex"], key="rag_source"
        )
        rag_article = fc3.text_input("GDPR Article", placeholder="Article 6 GDPR", key="rag_article")
        rag_top_n = st.slider("Context documents (top-N)", min_value=3, max_value=15, value=8, key="rag_top_n")
        rag_no_llm = st.checkbox(
            "Retrieval only (skip LLM — shows matched cases without generating an answer)",
            value=False, key="rag_no_llm",
        )

    ask_btn = st.button("Ask JurisMind", type="primary", use_container_width=True)

    # ── Session state for RAG results ─────────────────────────────────────────────

    if "rag_result" not in st.session_state:
        st.session_state.rag_result = None
    if "rag_contexts" not in st.session_state:
        st.session_state.rag_contexts = None
    if "rag_error" not in st.session_state:
        st.session_state.rag_error = None

    if ask_btn and query_text.strip():
        filters: dict = {}
        if rag_jurisdiction.strip():
            filters["jurisdiction"] = rag_jurisdiction.strip()
        if rag_source != "(any)":
            filters["source"] = rag_source
        if rag_article.strip():
            filters["gdpr_article"] = rag_article.strip()

        try:
            conn = get_conn()
            bedrock = get_bedrock_client()
            t0 = time.monotonic()

            # FASE 1 — Retrieval (sync, always runs)
            with st.spinner("Searching case law..."):
                result = rag_module.query(
                    conn=conn,
                    bedrock_client=bedrock,
                    user_id="streamlit-demo",
                    query_text=query_text.strip(),
                    filters=filters,
                    top_n=rag_top_n,
                    no_llm=True,  # retrieval only first
                )
            retrieval_ms = int((time.monotonic() - t0) * 1000)

            if rag_no_llm:
                # Retrieval-only mode: store result and stop here
                st.session_state.rag_result = result
                st.session_state.rag_error = None
            else:
                # FASE 2 — Streaming generation
                st.markdown("---")
                st.markdown(f"#### {len(result.citations)} cases found ({retrieval_ms} ms retrieval)")
                for c in result.citations[:5]:
                    fine_str = f" · {fmt_eur(c.get('fine_amount'))}" if c.get("fine_amount") else ""
                    st.caption(f"- {c.get('title', '—')} | {c.get('authority', '')} | {c.get('year', '')}{fine_str}")

                corpus_index = rag_module.load_corpus_index()
                system_p, user_p = rag_module.build_prompt(
                    query_text.strip(), result.citations, [], corpus_index
                )

                st.markdown("#### Answer")
                with st.chat_message("assistant"):
                    full_response = st.write_stream(
                        rag_module.call_llm_stream(system_p, user_p)
                    )

                latency_total = int((time.monotonic() - t0) * 1000)
                st.caption(f"Retrieval: {retrieval_ms} ms · Total: {latency_total} ms · Session: (streaming)")

                # Store result with full response for expander display below
                from dataclasses import replace as _dc_replace
                st.session_state.rag_result = _dc_replace(result, response=full_response, session_id="streaming")
                st.session_state.rag_error = None

        except Exception as e:
            st.session_state.rag_error = str(e)
            st.session_state.rag_result = None

    # ── Display results ───────────────────────────────────────────────────────────

    if st.session_state.rag_error:
        st.error(f"Error: {st.session_state.rag_error}")

    if st.session_state.rag_result:
        result = st.session_state.rag_result
        is_retrieval_only = result.session_id == "dry-run"
        is_streaming = result.session_id == "streaming"

        if is_retrieval_only:
            st.markdown("---")
            st.markdown(f"#### Retrieved Cases ({len(result.citations)})")
            st.caption("Retrieval-only mode — showing matched cases ranked by hybrid search (vector + BM25).")
            for i, c in enumerate(result.citations, start=1):
                fine_str = f"💶 {fmt_eur(c.get('fine_amount'))}" if c.get("fine_amount") else ""
                with st.container(border=True):
                    cols = st.columns([7, 3])
                    with cols[0]:
                        st.markdown(f"**[{i}] {c.get('title', '—')}**")
                        meta = " · ".join(filter(None, [
                            c.get("authority", ""),
                            c.get("jurisdiction", ""),
                            str(c.get("year", "")) if c.get("year") else "",
                            fine_str,
                        ]))
                        if meta:
                            st.caption(meta)
                        if c.get("articles"):
                            st.caption("Articles: " + ", ".join(c["articles"][:5]))
                        if c.get("snippet"):
                            st.markdown(f"> {c['snippet'][:300]}…")
                    with cols[1]:
                        url = c.get("source_url")
                        if url:
                            st.link_button("View case →", url, use_container_width=True)
            st.caption(f"Latency: {result.latency_ms} ms")

        elif is_streaming:
            # Answer already rendered inline above during streaming.
            # On subsequent reruns just show the stored response + citations.
            if not ask_btn:
                st.markdown("---")
                st.markdown("#### Answer")
                st.markdown(result.response)
            if result.citations:
                with st.expander(f"Sources ({len(result.citations)} documents)", expanded=False):
                    for i, c in enumerate(result.citations, start=1):
                        label = f"[{c.get('authority', '')} — {c.get('title', '')} — {c.get('year', '')}]"
                        url = c.get("source_url")
                        if url:
                            st.markdown(f"- [{label}]({url})")
                        else:
                            st.markdown(f"- {label}")

        else:
            st.markdown("---")
            st.markdown("#### Answer")
            st.markdown(result.response)
            if result.citations:
                st.markdown("**Sources cited:**")
                for c in result.citations:
                    label = f"[{c.get('authority', '')} — {c.get('title', '')} — {c.get('year', '')}]"
                    url = c.get("source_url")
                    if url:
                        st.markdown(f"- [{label}]({url})")
                    else:
                        st.markdown(f"- {label}")
            with st.expander(f"Retrieved context ({len(result.citations)} documents)", expanded=False):
                for i, c in enumerate(result.citations, start=1):
                    st.markdown(f"**[{i}]** {c.get('title', '')} | {c.get('authority', '')} | {c.get('year', '')}")
            st.caption(f"Latency: {result.latency_ms} ms · Session: {result.session_id}")

    elif ask_btn and not query_text.strip():
        st.warning("Please enter a research question.")
