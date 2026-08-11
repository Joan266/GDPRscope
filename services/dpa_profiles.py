"""
DPA Behavioral Profiles — generates enforcement intelligence per jurisdiction.
Queries documents + case_factors to build statistical profiles of each DPA.
"""
import logging
from dataclasses import dataclass, field
from typing import Any

import psycopg

log = logging.getLogger(__name__)

import re

_ART_RE = re.compile(
    r"(?:Art(?:icle)?\.?\s*)"       # "Art." or "Article "
    r"(\d+)"                         # article number
    r"(\s*\([^)]*\))?"               # optional paragraph e.g. (1)
    r"(\s*[a-z]\))?",                # optional sub-point e.g. f)
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


@dataclass
class DPAProfile:
    jurisdiction: str
    total_cases: int
    cases_with_fine: int
    total_fines_eur: int
    median_fine: float
    mean_fine: float
    max_fine: int
    max_fine_case: str
    top_articles: list[dict[str, Any]]       # [{article, count, pct}]
    top_sectors: list[dict[str, Any]]        # [{sector, count, pct}]
    yearly_trend: list[dict[str, Any]]       # [{year, count, median}]
    trend_direction: str                      # 'increasing' | 'stable' | 'decreasing'
    cooperation_credit: dict[str, Any] | None  # {median_coop, median_no_coop, reduction_pct, n}
    intent_split: dict[str, int] | None       # {intentional: n, negligent: n}
    recent_cases: list[dict[str, Any]]        # [{title, fine, date, articles}]
    factors_count: int


def generate_dpa_profile(conn: psycopg.Connection, jurisdiction: str) -> DPAProfile | None:
    """Generate a full behavioral profile for a DPA."""
    cur = conn.cursor()

    # ── Basic stats ──
    cur.execute("""
        SELECT count(*) as total,
               count(*) FILTER (WHERE fine_amount > 0) as with_fine,
               COALESCE(sum(fine_amount), 0) as total_fines,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fine_amount)
                   FILTER (WHERE fine_amount > 0) as median,
               COALESCE(avg(fine_amount) FILTER (WHERE fine_amount > 0), 0) as mean,
               COALESCE(max(fine_amount), 0) as max_fine
        FROM documents WHERE jurisdiction = %s
    """, (jurisdiction,))
    row = cur.fetchone()
    if not row or row[0] == 0:
        return None

    total, with_fine, total_fines, median, mean, max_fine = row

    # ── Max fine case title ──
    cur.execute("""
        SELECT title FROM documents
        WHERE jurisdiction = %s AND fine_amount = %s
        LIMIT 1
    """, (jurisdiction, max_fine))
    max_case_row = cur.fetchone()
    max_fine_case = max_case_row[0] if max_case_row else ""

    # ── Top articles ──
    top_articles = _get_top_articles(cur, jurisdiction, with_fine)

    # ── Top sectors ──
    top_sectors = _get_top_sectors(cur, jurisdiction, with_fine)

    # ── Yearly trend ──
    yearly_trend, trend_direction = _get_yearly_trend(cur, jurisdiction)

    # ── Cooperation credit ──
    cooperation_credit = _get_cooperation_credit(cur, jurisdiction)

    # ── Intent split ──
    intent_split = _get_intent_split(cur, jurisdiction)

    # ── Recent cases ──
    recent_cases = _get_recent_cases(cur, jurisdiction)

    # ── Factors count ──
    cur.execute("""
        SELECT count(*) FROM case_factors cf
        JOIN documents d ON d.id = cf.document_id
        WHERE d.jurisdiction = %s
    """, (jurisdiction,))
    factors_count = cur.fetchone()[0]

    return DPAProfile(
        jurisdiction=jurisdiction,
        total_cases=total,
        cases_with_fine=with_fine,
        total_fines_eur=total_fines,
        median_fine=float(median) if median else 0,
        mean_fine=float(mean),
        max_fine=max_fine,
        max_fine_case=max_fine_case,
        top_articles=top_articles,
        top_sectors=top_sectors,
        yearly_trend=yearly_trend,
        trend_direction=trend_direction,
        cooperation_credit=cooperation_credit,
        intent_split=intent_split,
        recent_cases=recent_cases,
        factors_count=factors_count,
    )


def _get_top_articles(
    cur: psycopg.Cursor, jurisdiction: str, with_fine: int
) -> list[dict[str, Any]]:
    """Top articles enforced by this DPA, with normalization to merge duplicates."""
    cur.execute("""
        SELECT art, count(*) as n
        FROM (
            SELECT unnest(gdpr_articles) as art
            FROM documents
            WHERE jurisdiction = %s AND fine_amount > 0
        ) sub
        GROUP BY art ORDER BY n DESC LIMIT 30
    """, (jurisdiction,))
    # Merge normalized articles
    merged: dict[str, int] = {}
    for raw_art, count in cur.fetchall():
        norm = _normalize_article(raw_art)
        if norm in ("", "Unknown"):
            continue
        merged[norm] = merged.get(norm, 0) + count
    total = max(with_fine, 1)
    sorted_arts = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:8]
    return [
        {"article": art, "count": n, "pct": round(n / total * 100, 1)}
        for art, n in sorted_arts
    ]


def _get_top_sectors(
    cur: psycopg.Cursor, jurisdiction: str, with_fine: int
) -> list[dict[str, Any]]:
    """Top sectors fined by this DPA."""
    cur.execute("""
        SELECT sector, count(*) as n
        FROM documents
        WHERE jurisdiction = %s AND fine_amount > 0 AND sector IS NOT NULL
        GROUP BY sector ORDER BY n DESC LIMIT 5
    """, (jurisdiction,))
    total = max(with_fine, 1)
    return [
        {"sector": r[0], "count": r[1], "pct": round(r[1] / total * 100, 1)}
        for r in cur.fetchall()
    ]


def _get_yearly_trend(
    cur: psycopg.Cursor, jurisdiction: str
) -> tuple[list[dict[str, Any]], str]:
    """Yearly enforcement trend + direction classification."""
    cur.execute("""
        SELECT decision_year, count(*) as n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fine_amount) as median
        FROM documents
        WHERE jurisdiction = %s AND fine_amount > 0
              AND decision_year IS NOT NULL AND decision_year >= 2019
        GROUP BY decision_year ORDER BY decision_year
    """, (jurisdiction,))
    rows = cur.fetchall()
    trend = [
        {"year": r[0], "count": r[1], "median": float(r[2]) if r[2] else 0}
        for r in rows
    ]

    # Classify trend direction from last 3 complete years
    direction = "stable"
    complete_years = [t for t in trend if t["year"] <= 2025]
    if len(complete_years) >= 3:
        recent = complete_years[-3:]
        medians = [y["median"] for y in recent]
        if medians[-1] > medians[0] * 1.3:
            direction = "increasing"
        elif medians[-1] < medians[0] * 0.7:
            direction = "decreasing"

    return trend, direction


def _get_cooperation_credit(
    cur: psycopg.Cursor, jurisdiction: str
) -> dict[str, Any] | None:
    """Measure cooperation impact on fines for this DPA."""
    cur.execute("""
        SELECT (cf.factor_f_cooperation->>'cooperated')::boolean as coop,
               count(*),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median
        FROM case_factors cf
        JOIN documents d ON d.id = cf.document_id
        WHERE d.jurisdiction = %s AND d.fine_amount > 0
              AND cf.factor_f_cooperation->>'cooperated' IS NOT NULL
        GROUP BY 1
    """, (jurisdiction,))
    rows = {r[0]: {"n": r[1], "median": float(r[2])} for r in cur.fetchall()}
    if True not in rows or False not in rows:
        return None
    # Need minimum 3 cases per group for statistical validity
    if rows[True]["n"] < 3 or rows[False]["n"] < 3:
        return None

    med_coop = rows[True]["median"]
    med_no_coop = rows[False]["median"]
    reduction = 0.0
    if med_no_coop > 0:
        reduction = round((1 - med_coop / med_no_coop) * 100, 1)

    return {
        "median_cooperated": med_coop,
        "median_not_cooperated": med_no_coop,
        "reduction_pct": reduction,
        "n_cooperated": rows[True]["n"],
        "n_not_cooperated": rows[False]["n"],
    }


def _get_intent_split(
    cur: psycopg.Cursor, jurisdiction: str
) -> dict[str, int] | None:
    """Count intentional vs negligent violations."""
    cur.execute("""
        SELECT cf.factor_b_intent->>'type' as intent, count(*)
        FROM case_factors cf
        JOIN documents d ON d.id = cf.document_id
        WHERE d.jurisdiction = %s AND d.fine_amount > 0
              AND cf.factor_b_intent->>'type' IN ('intentional', 'negligent')
        GROUP BY 1
    """, (jurisdiction,))
    rows = cur.fetchall()
    if not rows:
        return None
    return {r[0]: r[1] for r in rows}


def _get_recent_cases(
    cur: psycopg.Cursor, jurisdiction: str
) -> list[dict[str, Any]]:
    """Most recent fined cases for this DPA."""
    cur.execute("""
        SELECT title, fine_amount, decision_date, gdpr_articles
        FROM documents
        WHERE jurisdiction = %s AND fine_amount > 0
        ORDER BY decision_date DESC NULLS LAST
        LIMIT 5
    """, (jurisdiction,))
    return [
        {
            "title": r[0],
            "fine": r[1],
            "date": str(r[2]) if r[2] else None,
            "articles": r[3] or [],
        }
        for r in cur.fetchall()
    ]


def compare_dpas(
    conn: psycopg.Connection, jurisdictions: list[str]
) -> list[DPAProfile]:
    """Generate profiles for multiple DPAs for side-by-side comparison."""
    return [
        p for j in jurisdictions
        if (p := generate_dpa_profile(conn, j)) is not None
    ]


def get_all_jurisdictions(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """List all jurisdictions with basic stats for dropdown/selector."""
    cur = conn.cursor()
    cur.execute("""
        SELECT jurisdiction, count(*) as total,
               count(*) FILTER (WHERE fine_amount > 0) as with_fine,
               COALESCE(sum(fine_amount), 0) as total_fines
        FROM documents
        WHERE jurisdiction IS NOT NULL
        GROUP BY jurisdiction
        ORDER BY total DESC
    """)
    return [
        {
            "jurisdiction": r[0],
            "total_cases": r[1],
            "cases_with_fine": r[2],
            "total_fines": r[3],
        }
        for r in cur.fetchall()
    ]


# ── CLI test ──
if __name__ == "__main__":
    import json
    import os
    import sys

    logging.basicConfig(level=logging.INFO)
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

    jurisdiction = sys.argv[1] if len(sys.argv) > 1 else "Spain"
    profile = generate_dpa_profile(conn, jurisdiction)
    if profile:
        print(f"\n{'='*60}")
        print(f"DPA PROFILE: {profile.jurisdiction}")
        print(f"{'='*60}")
        print(f"Total cases:    {profile.total_cases}")
        print(f"Cases w/ fine:  {profile.cases_with_fine}")
        print(f"Total fines:    EUR {profile.total_fines_eur:,}")
        print(f"Median fine:    EUR {profile.median_fine:,.0f}")
        print(f"Mean fine:      EUR {profile.mean_fine:,.0f}")
        print(f"Max fine:       EUR {profile.max_fine:,}")
        print(f"Max fine case:  {profile.max_fine_case[:80]}")
        print(f"Trend:          {profile.trend_direction}")
        print(f"Factors:        {profile.factors_count}")
        print(f"\nTop articles:")
        for a in profile.top_articles[:5]:
            print(f"  {a['article']:30s}  {a['count']:3d} ({a['pct']}%)")
        print(f"\nTop sectors:")
        for s in profile.top_sectors[:5]:
            print(f"  {s['sector']:35s}  {s['count']:3d} ({s['pct']}%)")
        print(f"\nYearly trend:")
        for y in profile.yearly_trend:
            print(f"  {y['year']}  n={y['count']:3d}  median=EUR {y['median']:>10,.0f}")
        if profile.cooperation_credit:
            cc = profile.cooperation_credit
            print(f"\nCooperation credit:")
            print(f"  Cooperated:     EUR {cc['median_cooperated']:>10,.0f} (n={cc['n_cooperated']})")
            print(f"  Not cooperated: EUR {cc['median_not_cooperated']:>10,.0f} (n={cc['n_not_cooperated']})")
            print(f"  Reduction:      {cc['reduction_pct']}%")
        if profile.intent_split:
            print(f"\nIntent split: {profile.intent_split}")
        print(f"\nRecent cases:")
        for c in profile.recent_cases:
            print(f"  [{c['date']}] EUR {c['fine']:>10,}  {c['title'][:60]}")
    else:
        print(f"No data for jurisdiction: {jurisdiction}")

    conn.close()
