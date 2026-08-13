"""
Intelligence Search — query routing + entity dossiers + article views.

Routes natural language queries to the best data retrieval strategy:
  entity      → controller_name ILIKE → full dossier with stats
  article     → gdpr_articles match → article enforcement view
  aggregation → sort/filter queries → ordered case list
  rag         → conceptual fallback → hybrid text+vector search
"""

import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

import psycopg

from db.rag import (
    GENERIC_CONTROLLER_TERMS,
    QueryIntent,
    extract_intent,
    resolve_entity_alias,
    sanitize_tsquery,
)

log = logging.getLogger(__name__)

_ART_RE = re.compile(
    r"(?:Art(?:icle)?\.?\s*)"
    r"(\d+)"
    r"(\s*\([^)]*\))?"
    r"(\s*[a-z]\))?",
    re.IGNORECASE,
)


def _normalize_article(raw: str) -> str:
    """Normalize article references to 'Art. N' or 'Art. N(x)' format."""
    m = _ART_RE.search(raw)
    if not m:
        return raw.strip()
    num = m.group(1)
    para = m.group(2).strip() if m.group(2) else ""
    sub = " " + m.group(3).strip() if m.group(3) else ""
    return f"Art. {num}{para}{sub}".replace("  ", " ")


# ---- Dataclasses ----

@dataclass
class RouteInfo:
    route: str          # "entity" | "article" | "aggregation" | "rag"
    matched_term: str

@dataclass
class CaseRow:
    title: str
    jurisdiction: str | None
    fine_amount: int | None
    gdpr_articles: list[str]
    decision_date: str | None
    decision_year: int | None
    sector: str | None
    controller_name: str | None
    summary_teaser: str | None

@dataclass
class EntityDossier:
    route_info: RouteInfo
    entity_name: str
    cases: list[CaseRow]
    total_cases: int
    total_fines: int
    median_fine: float
    max_fine: int
    top_articles: list[dict[str, Any]]
    jurisdictions: list[dict[str, Any]]
    yearly_trend: list[dict[str, Any]]

@dataclass
class ArticleView:
    route_info: RouteInfo
    article_label: str
    cases: list[CaseRow]
    total_cases: int
    total_fines: int
    median_fine: float
    top_dpas: list[dict[str, Any]]
    top_sectors: list[dict[str, Any]]

@dataclass
class AggregationView:
    route_info: RouteInfo
    cases: list[CaseRow]
    total_results: int
    total_fines: int
    filters_applied: dict[str, Any]

@dataclass
class RagView:
    route_info: RouteInfo
    results: list[dict[str, Any]]
    total_results: int


IntelligenceResult = EntityDossier | ArticleView | AggregationView | RagView


# ---- Row helper ----

def _row_to_case(row: tuple) -> CaseRow:
    return CaseRow(
        title=row[0] or "",
        jurisdiction=row[1],
        fine_amount=row[2],
        gdpr_articles=row[3] or [],
        decision_date=str(row[4]) if row[4] else None,
        decision_year=row[5],
        sector=row[6],
        controller_name=row[7],
        summary_teaser=row[8],
    )


_CASE_COLS = (
    "d.title, d.jurisdiction, d.fine_amount, d.gdpr_articles, "
    "d.decision_date, d.decision_year, d.sector, d.controller_name, "
    "d.summary_teaser"
)


# ---- Orchestrator ----

def run_intelligence_query(
    conn: psycopg.Connection, query_text: str
) -> IntelligenceResult:
    """Route a natural language query to the best retrieval strategy."""
    intent = _safe_extract_intent(query_text)

    # Entity route
    if intent and intent.controller_name:
        patterns = resolve_entity_alias(intent.controller_name)
        if patterns:
            return _build_entity_dossier(conn, intent, patterns)

    # Article route
    if intent and intent.gdpr_articles:
        return _build_article_view(conn, intent)

    # Aggregation route
    if intent and (intent.sort_by or intent.has_fine):
        return _build_aggregation_view(conn, intent)

    # RAG fallback
    return _build_rag_view(conn, query_text)


def _safe_extract_intent(query_text: str) -> QueryIntent | None:
    try:
        return extract_intent(query_text)
    except Exception as exc:
        log.debug("Intent extraction failed: %s", exc)
        return None


# ---- Entity Dossier ----

def _build_entity_dossier(
    conn: psycopg.Connection,
    intent: QueryIntent,
    patterns: list[str],
) -> EntityDossier:
    cur = conn.cursor()
    ilike_clause, ilike_params = _ilike_clause(patterns)

    # Cases
    cur.execute(
        f"SELECT {_CASE_COLS} FROM documents d "
        f"WHERE {ilike_clause} "
        "ORDER BY d.fine_amount DESC NULLS LAST LIMIT 100",
        ilike_params,
    )
    cases = [_row_to_case(r) for r in cur.fetchall()]

    # Stats
    cur.execute(
        f"SELECT count(*), COALESCE(sum(fine_amount), 0), "
        f"max(fine_amount) FROM documents d WHERE {ilike_clause}",
        ilike_params,
    )
    stats = cur.fetchone()
    total_cases, total_fines, max_fine = stats[0], int(stats[1] or 0), int(stats[2] or 0)

    # Median (manual — percentile_cont may not work on all DBs with filters)
    fines = [c.fine_amount for c in cases if c.fine_amount and c.fine_amount > 0]
    median_fine = float(statistics.median(fines)) if fines else 0.0

    # Top articles
    cur.execute(
        f"SELECT unnest(d.gdpr_articles) as art, count(*) as n "
        f"FROM documents d WHERE {ilike_clause} "
        "GROUP BY art ORDER BY n DESC LIMIT 30",
        ilike_params,
    )
    top_articles = _merge_articles(cur.fetchall())

    # Cross-jurisdiction
    cur.execute(
        f"SELECT d.jurisdiction, count(*), COALESCE(sum(d.fine_amount), 0) "
        f"FROM documents d WHERE {ilike_clause} AND d.jurisdiction IS NOT NULL "
        "GROUP BY d.jurisdiction ORDER BY sum(d.fine_amount) DESC NULLS LAST",
        ilike_params,
    )
    jurisdictions = [
        {"jurisdiction": r[0], "count": r[1], "total_fines": int(r[2] or 0)}
        for r in cur.fetchall()
    ]

    # Yearly trend
    cur.execute(
        f"SELECT d.decision_year, count(*), COALESCE(sum(d.fine_amount), 0) "
        f"FROM documents d WHERE {ilike_clause} AND d.decision_year IS NOT NULL "
        "GROUP BY d.decision_year ORDER BY d.decision_year",
        ilike_params,
    )
    yearly_trend = [
        {"year": r[0], "count": r[1], "total_fines": int(r[2] or 0)}
        for r in cur.fetchall()
    ]

    entity_name = intent.controller_name or patterns[0]
    return EntityDossier(
        route_info=RouteInfo(route="entity", matched_term=entity_name),
        entity_name=entity_name,
        cases=cases,
        total_cases=total_cases,
        total_fines=total_fines,
        median_fine=median_fine,
        max_fine=max_fine,
        top_articles=top_articles,
        jurisdictions=jurisdictions,
        yearly_trend=yearly_trend,
    )


# ---- Article View ----

def _build_article_view(
    conn: psycopg.Connection, intent: QueryIntent
) -> ArticleView:
    cur = conn.cursor()
    art = intent.gdpr_articles[0]
    art_num = re.search(r"\d+", art)
    pattern = f"%{art_num.group()}%" if art_num else f"%{art}%"

    art_filter = (
        "EXISTS (SELECT 1 FROM unnest(d.gdpr_articles) AS a WHERE a ILIKE %s)"
    )
    base_params: list[Any] = [pattern]

    # Optional jurisdiction filter
    juris_clause = ""
    if intent.jurisdiction:
        juris_clause = " AND d.jurisdiction = %s"
        base_params.append(intent.jurisdiction)

    # Cases
    cur.execute(
        f"SELECT {_CASE_COLS} FROM documents d "
        f"WHERE {art_filter}{juris_clause} "
        "ORDER BY d.fine_amount DESC NULLS LAST LIMIT 100",
        base_params,
    )
    cases = [_row_to_case(r) for r in cur.fetchall()]

    # Stats
    stat_params: list[Any] = [pattern]
    if intent.jurisdiction:
        stat_params.append(intent.jurisdiction)
    cur.execute(
        f"SELECT count(*), COALESCE(sum(fine_amount), 0) "
        f"FROM documents d WHERE {art_filter}{juris_clause}",
        stat_params,
    )
    stats = cur.fetchone()
    total_cases, total_fines = stats[0], int(stats[1] or 0)

    fines = [c.fine_amount for c in cases if c.fine_amount and c.fine_amount > 0]
    median_fine = float(statistics.median(fines)) if fines else 0.0

    # Top DPAs
    cur.execute(
        f"SELECT d.jurisdiction, count(*), COALESCE(sum(d.fine_amount), 0) "
        f"FROM documents d WHERE {art_filter} AND d.jurisdiction IS NOT NULL "
        "GROUP BY d.jurisdiction ORDER BY count(*) DESC LIMIT 10",
        [pattern],
    )
    top_dpas = [
        {"jurisdiction": r[0], "count": r[1], "total_fines": int(r[2] or 0)}
        for r in cur.fetchall()
    ]

    # Top sectors
    cur.execute(
        f"SELECT d.sector, count(*) "
        f"FROM documents d WHERE {art_filter} "
        "AND d.sector IS NOT NULL "
        "GROUP BY d.sector ORDER BY count(*) DESC LIMIT 8",
        [pattern],
    )
    top_sectors = [
        {"sector": r[0], "count": r[1]}
        for r in cur.fetchall()
    ]

    article_label = f"Article {art_num.group()}" if art_num else art
    return ArticleView(
        route_info=RouteInfo(route="article", matched_term=article_label),
        article_label=article_label,
        cases=cases,
        total_cases=total_cases,
        total_fines=total_fines,
        median_fine=median_fine,
        top_dpas=top_dpas,
        top_sectors=top_sectors,
    )


# ---- Aggregation View ----

def _build_aggregation_view(
    conn: psycopg.Connection, intent: QueryIntent
) -> AggregationView:
    cur = conn.cursor()
    clauses: list[str] = []
    params: list[Any] = []
    filters_applied: dict[str, Any] = {}

    if intent.jurisdiction:
        clauses.append("d.jurisdiction = %s")
        params.append(intent.jurisdiction)
        filters_applied["jurisdiction"] = intent.jurisdiction

    if intent.year_min is not None:
        clauses.append("d.decision_year >= %s")
        params.append(intent.year_min)
        filters_applied["year_min"] = intent.year_min

    if intent.year_max is not None:
        clauses.append("d.decision_year <= %s")
        params.append(intent.year_max)
        filters_applied["year_max"] = intent.year_max

    if intent.has_fine:
        clauses.append("d.fine_amount IS NOT NULL AND d.fine_amount > 0")
        filters_applied["has_fine"] = True

    where = " AND ".join(clauses) if clauses else "TRUE"

    order = "d.fine_amount DESC NULLS LAST"
    if intent.sort_by == "date_desc":
        order = "d.decision_date DESC NULLS LAST"
        filters_applied["sort"] = "date_desc"
    else:
        filters_applied["sort"] = "fine_desc"

    cur.execute(
        f"SELECT {_CASE_COLS} FROM documents d "
        f"WHERE {where} ORDER BY {order} LIMIT 100",
        params,
    )
    cases = [_row_to_case(r) for r in cur.fetchall()]

    # Total count + fines
    cur.execute(
        f"SELECT count(*), COALESCE(sum(fine_amount), 0) "
        f"FROM documents d WHERE {where}",
        params,
    )
    stats = cur.fetchone()

    return AggregationView(
        route_info=RouteInfo(
            route="aggregation",
            matched_term=intent.sort_by or "fine_desc",
        ),
        cases=cases,
        total_results=stats[0],
        total_fines=int(stats[1] or 0),
        filters_applied=filters_applied,
    )


# ---- RAG View (BM25 fallback — no LLM) ----

def _build_rag_view(
    conn: psycopg.Connection, query_text: str
) -> RagView:
    cur = conn.cursor()
    safe = sanitize_tsquery(query_text)
    if not safe:
        return RagView(
            route_info=RouteInfo(route="rag", matched_term=query_text),
            results=[],
            total_results=0,
        )

    cur.execute(
        "SELECT d.title, d.jurisdiction, d.fine_amount, d.gdpr_articles, "
        "d.decision_date, d.sector, d.summary_teaser, d.controller_name "
        "FROM documents d "
        "WHERE d.search_vector @@ plainto_tsquery('english', %s) "
        "ORDER BY ts_rank(d.search_vector, plainto_tsquery('english', %s)) DESC "
        "LIMIT 50",
        [safe, safe],
    )
    results = [
        {
            "title": r[0] or "",
            "jurisdiction": r[1],
            "fine_amount": r[2],
            "gdpr_articles": r[3] or [],
            "decision_date": str(r[4]) if r[4] else None,
            "sector": r[5],
            "summary_teaser": r[6],
            "controller_name": r[7],
        }
        for r in cur.fetchall()
    ]

    return RagView(
        route_info=RouteInfo(route="rag", matched_term=query_text),
        results=results,
        total_results=len(results),
    )


# ---- Helpers ----

def _ilike_clause(patterns: list[str]) -> tuple[str, list[str]]:
    """Build ILIKE ANY clause for controller_name patterns."""
    return (
        "d.controller_name ILIKE ANY(%s)",
        [[f"%{p}%" for p in patterns]],
    )


def _merge_articles(rows: list[tuple]) -> list[dict[str, Any]]:
    """Normalize and merge duplicate article references."""
    merged: dict[str, int] = {}
    for raw_art, count in rows:
        norm = _normalize_article(raw_art)
        if norm in ("", "Unknown"):
            continue
        merged[norm] = merged.get(norm, 0) + count
    sorted_arts = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:15]
    return [{"article": art, "count": n} for art, n in sorted_arts]
