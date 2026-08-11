"""
JurisMind — Prosecution Simulator (Fine Estimation Engine)

EDPB 5-step methodology applied to real enforcement data:
  1. Categorize violation (article → severity)
  2. Calculate starting point (turnover × severity band)
  3. Evaluate Art. 83(2) factors (aggravating/mitigating from precedents)
  4. Verify legal maximums
  5. Proportionality adjustment (statistical range from real cases)

Returns a range (P25-P75) based on matching precedents, not a single number.

Variable importance (eta-squared, measured on 3,841 fined cases):
  Jurisdiction: 21.6%  |  Sector: 18.4%  |  Article: 14.4%
  Cooperation: 6.6%    |  Sensitive data: 5.5%  |  Self-reported: 5.5%
  Turnover: ~30-50% (estimated, rarely public — user-provided)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

import psycopg

# Recency: precedents older than this get downweighted
_RECENCY_BASELINE_YEAR = 2020
_RECENCY_DECAY = 0.15  # 15% penalty per year before baseline


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class SimulationInput:
    articles_violated: list[str]
    jurisdiction: str | None = None
    sector: str | None = None
    turnover_eur: int | None = None
    data_subjects_affected: int | None = None
    data_categories: str | None = None  # "health", "financial", "children", etc.
    intentional: bool = False
    prior_violations: bool = False
    cooperated: bool = True
    notified_voluntarily: bool = True
    corrective_measures: bool = True
    prior_security_measures: bool = True


@dataclass
class Precedent:
    title: str
    jurisdiction: str
    fine_amount: int
    articles: list[str]
    sector: str | None
    decision_year: int | None = None
    similarity_score: float = 0.0
    factor_summary: str | None = None


@dataclass
class FactorImpact:
    factor: str
    direction: str  # "mitigating" or "aggravating"
    avg_reduction_pct: float | None = None
    cases_count: int = 0


@dataclass
class SimulationResult:
    estimated_range: dict[str, float]
    methodology: dict[str, Any]
    precedents: list[Precedent]
    factor_impacts: list[FactorImpact]
    dpa_comparison: dict[str, dict]
    confidence: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = ("This is a statistical range based on public enforcement "
                       "precedents, not a prediction. Actual fines depend on DPA "
                       "discretion and company turnover.")


# ── Step 1: Categorize violation ──────────────────────────────────────────────

# GDPR Article → severity category based on Art. 83(4-6)
# Art. 83(5-6) violations: up to 20M EUR or 4% turnover → "severe"
# Art. 83(4) violations: up to 10M EUR or 2% turnover → "moderate"
SEVERE_ARTICLES = {
    "5", "6", "7", "9", "12", "13", "14", "15", "16", "17", "18", "19",
    "20", "21", "22",  # Data subject rights + lawfulness principles
    "44", "45", "46", "47", "48", "49",  # International transfers
    "58",  # Non-compliance with DPA orders
}
MODERATE_ARTICLES = {
    "8", "11", "25", "26", "27", "28", "29", "30", "31", "32", "33",
    "34", "35", "36", "37", "38", "39", "41", "42", "43",
}


def categorize_violation(articles: list[str]) -> dict:
    """Step 1: Classify articles into severity tiers."""
    normalized = [a.replace("Art. ", "").replace("Article ", "").split("(")[0].strip()
                  for a in articles]

    severe = [a for a in normalized if a in SEVERE_ARTICLES]
    moderate = [a for a in normalized if a in MODERATE_ARTICLES]

    if severe:
        severity = "severe"
        max_static = 20_000_000
        max_turnover_pct = 0.04
        tier = "Art. 83(5-6)"
    elif moderate:
        severity = "moderate"
        max_static = 10_000_000
        max_turnover_pct = 0.02
        tier = "Art. 83(4)"
    else:
        severity = "moderate"
        max_static = 10_000_000
        max_turnover_pct = 0.02
        tier = "Art. 83(4) (default)"

    return {
        "severity": severity,
        "tier": tier,
        "max_static_eur": max_static,
        "max_turnover_pct": max_turnover_pct,
        "severe_articles": severe,
        "moderate_articles": moderate,
    }


# ── Step 2: Starting point ───────────────────────────────────────────────────

def calculate_starting_point(category: dict, turnover: int | None) -> dict:
    """Step 2: Calculate starting point based on turnover and severity."""
    max_static = category["max_static_eur"]

    if turnover and turnover > 0:
        max_turnover = int(turnover * category["max_turnover_pct"])
        legal_max = max(max_static, max_turnover)
    else:
        legal_max = max_static

    if category["severity"] == "severe":
        # EDPB: severe violations start at 20-50% of max
        low = int(legal_max * 0.10)
        mid = int(legal_max * 0.20)
        high = int(legal_max * 0.50)
    else:
        # Moderate: 0-10% of max
        low = int(legal_max * 0.02)
        mid = int(legal_max * 0.05)
        high = int(legal_max * 0.10)

    return {
        "legal_max": legal_max,
        "starting_low": low,
        "starting_mid": mid,
        "starting_high": high,
    }


# ── Step 3: Find precedents + factor analysis ────────────────────────────────

PRECEDENT_SQL = """
    SELECT d.title, d.jurisdiction, d.fine_amount, d.gdpr_articles,
           d.sector, cf.overall_assessment, d.decision_year
    FROM documents d
    LEFT JOIN case_factors cf ON cf.document_id = d.id
    WHERE d.fine_amount > 0
      AND d.gdpr_articles IS NOT NULL
"""


def _build_precedent_query(params: SimulationInput) -> tuple[str, list]:
    """Build SQL query for finding matching precedents."""
    sql = PRECEDENT_SQL
    args: list = []
    conditions: list[str] = []

    # Match by articles (any overlap)
    if params.articles_violated:
        normalized = [a.replace("Art. ", "").replace("Article ", "").split("(")[0].strip()
                      for a in params.articles_violated]
        conditions.append("d.gdpr_articles && %s::text[]")
        args.append(normalized)

    if params.jurisdiction:
        conditions.append("d.jurisdiction = %s")
        args.append(params.jurisdiction)

    if params.sector:
        conditions.append("d.sector = %s")
        args.append(params.sector)

    if conditions:
        sql += " AND " + " AND ".join(conditions)

    sql += " ORDER BY d.fine_amount DESC LIMIT 200"
    return sql, args


def _normalize_article(art: str) -> str:
    """Normalize article reference: '32' → 'Art. 32', 'Article 32 GDPR' → 'Art. 32'."""
    art = art.strip()
    # Remove "GDPR" suffix
    art = art.replace(" GDPR", "")
    # Extract just the number(+subsection)
    art = art.replace("Article ", "").replace("Art. ", "").replace("Art.", "")
    return art.strip()


def _build_article_patterns(articles: list[str]) -> list[str]:
    """Build patterns that match DB format 'Article NN GDPR'.

    Returns list like ['%Article 32%', '%Article 33%'] for use with ANY/LIKE.
    """
    patterns = []
    for a in articles:
        num = _normalize_article(a)
        # Match "Article 32" anywhere in the array elements
        patterns.append(f"%{num}%")
    return patterns


def find_precedents(conn: psycopg.Connection, params: SimulationInput) -> list[Precedent]:
    """Find matching precedent cases with cascading relaxation.

    Tries strict match first (articles + jurisdiction + sector), then
    progressively relaxes filters until enough precedents are found.
    """
    input_articles = set(_normalize_article(a) for a in params.articles_violated)
    # Build array of patterns for array overlap using array_to_string
    article_nums = list(input_articles)
    cur = conn.cursor()

    # Build article match condition: checks if any article in the array
    # contains any of the input article numbers
    # e.g. "Article 32 GDPR" matches input "32"
    art_conditions = " OR ".join(
        f"array_to_string(d.gdpr_articles, '||') LIKE %s" for _ in article_nums
    )
    art_clause = f"({art_conditions})" if art_conditions else "TRUE"
    art_params = [f"%Art%{num}%" for num in article_nums]

    # Cascading queries: strict → relaxed
    queries = [
        # 1. Exact: articles + jurisdiction + sector
        (PRECEDENT_SQL + f" AND {art_clause} AND d.jurisdiction = %s AND d.sector = %s ORDER BY d.fine_amount DESC LIMIT 200",
         art_params + [params.jurisdiction, params.sector]),
        # 2. articles + jurisdiction
        (PRECEDENT_SQL + f" AND {art_clause} AND d.jurisdiction = %s ORDER BY d.fine_amount DESC LIMIT 200",
         art_params + [params.jurisdiction]),
        # 3. articles + sector
        (PRECEDENT_SQL + f" AND {art_clause} AND d.sector = %s ORDER BY d.fine_amount DESC LIMIT 200",
         art_params + [params.sector]),
        # 4. articles only
        (PRECEDENT_SQL + f" AND {art_clause} ORDER BY d.fine_amount DESC LIMIT 200",
         art_params),
    ]

    # Skip queries with None params
    rows = []
    for sql, args in queries:
        if any(a is None for a in args):
            continue
        cur.execute(sql, args)
        rows = cur.fetchall()
        if len(rows) >= 10:
            break

    precedents = []
    seen_ids = set()
    for r in rows:
        title = r[0]
        if title in seen_ids:
            continue
        seen_ids.add(title)

        case_articles = set(_normalize_article(a) for a in (r[3] or []))
        overlap = len(case_articles & input_articles)
        total = len(case_articles | input_articles) or 1
        article_sim = overlap / total  # Jaccard: base component

        # Eta²-weighted bonuses (measured importance)
        juris_match = 1.0 if r[1] == params.jurisdiction else 0.0
        sector_match = 1.0 if r[4] == params.sector else 0.0

        # Weighted composite: article(14.4%) + jurisdiction(21.6%) + sector(18.4%)
        # Normalize so weights sum to 1.0
        w_art, w_jur, w_sec = 0.144, 0.216, 0.184
        w_total = w_art + w_jur + w_sec
        sim = (w_art * article_sim + w_jur * juris_match + w_sec * sector_match) / w_total

        # Recency weighting: newer cases get higher scores
        decision_year = r[6]
        if decision_year and decision_year < _RECENCY_BASELINE_YEAR:
            years_old = _RECENCY_BASELINE_YEAR - decision_year
            recency_factor = max(0.3, 1.0 - _RECENCY_DECAY * years_old)
            sim *= recency_factor

        precedents.append(Precedent(
            title=title,
            jurisdiction=r[1],
            fine_amount=r[2],
            articles=r[3] or [],
            sector=r[4],
            decision_year=decision_year,
            similarity_score=round(sim, 3),
            factor_summary=r[5],
        ))

    precedents.sort(key=lambda p: (-p.similarity_score, -p.fine_amount))
    return precedents


def analyze_factor_impacts(conn: psycopg.Connection,
                           params: SimulationInput) -> list[FactorImpact]:
    """Analyze how aggravating/mitigating factors affected fines historically."""
    impacts: list[FactorImpact] = []

    # Cooperation impact
    cur = conn.cursor()
    cur.execute("""
        SELECT
            CASE WHEN (cf.factor_f_cooperation->>'cooperated')::boolean THEN 'cooperated'
                 ELSE 'not_cooperated' END as coop,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median_fine,
            count(*)
        FROM case_factors cf
        JOIN documents d ON d.id = cf.document_id
        WHERE cf.factor_f_cooperation IS NOT NULL
          AND cf.factor_f_cooperation->>'cooperated' IS NOT NULL
        GROUP BY 1
    """)
    coop_rows = cur.fetchall()
    coop_map = {r[0]: (r[1], r[2]) for r in coop_rows}
    if "cooperated" in coop_map and "not_cooperated" in coop_map:
        coop_med = coop_map["cooperated"][0]
        no_coop_med = coop_map["not_cooperated"][0]
        if no_coop_med > 0:
            reduction = ((no_coop_med - coop_med) / no_coop_med) * 100
            impacts.append(FactorImpact(
                factor="Cooperation with DPA",
                direction="mitigating",
                avg_reduction_pct=round(reduction, 1),
                cases_count=coop_map["cooperated"][1],
            ))

    # Intent impact
    cur.execute("""
        SELECT
            cf.factor_b_intent->>'type' as intent_type,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median_fine,
            count(*)
        FROM case_factors cf
        JOIN documents d ON d.id = cf.document_id
        WHERE cf.factor_b_intent IS NOT NULL
          AND cf.factor_b_intent->>'type' IN ('intentional', 'negligent')
        GROUP BY 1
    """)
    for r in cur.fetchall():
        impacts.append(FactorImpact(
            factor=f"Intent: {r[0]}",
            direction="aggravating" if r[0] == "intentional" else "neutral",
            avg_reduction_pct=None,
            cases_count=r[2],
        ))

    # Sensitive data impact
    cur.execute("""
        SELECT
            CASE WHEN (cf.factor_g_data_categories->>'sensitive')::boolean THEN 'sensitive'
                 ELSE 'non_sensitive' END as cat,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median_fine,
            count(*)
        FROM case_factors cf
        JOIN documents d ON d.id = cf.document_id
        WHERE cf.factor_g_data_categories IS NOT NULL
          AND cf.factor_g_data_categories->>'sensitive' IS NOT NULL
        GROUP BY 1
    """)
    sens_rows = cur.fetchall()
    sens_map = {r[0]: (r[1], r[2]) for r in sens_rows}
    if "sensitive" in sens_map and "non_sensitive" in sens_map:
        sens_med = sens_map["sensitive"][0]
        nonsens_med = sens_map["non_sensitive"][0]
        if nonsens_med > 0:
            increase = ((sens_med - nonsens_med) / nonsens_med) * 100
            impacts.append(FactorImpact(
                factor="Sensitive data categories",
                direction="aggravating",
                avg_reduction_pct=round(-increase, 1),
                cases_count=sens_map["sensitive"][1],
            ))

    return impacts


def get_dpa_comparison(conn: psycopg.Connection,
                       articles: list[str]) -> dict[str, dict]:
    """Compare median fines across DPAs for given articles."""
    article_nums = [_normalize_article(a) for a in articles]
    art_conditions = " OR ".join(
        f"array_to_string(d.gdpr_articles, '||') LIKE %s" for _ in article_nums
    )
    art_clause = f"({art_conditions})" if art_conditions else "TRUE"
    art_params = [f"%Art%{num}%" for num in article_nums]

    cur = conn.cursor()
    cur.execute(f"""
        SELECT d.jurisdiction,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median_fine,
               count(*) as cases
        FROM documents d
        WHERE d.fine_amount > 0
          AND d.gdpr_articles IS NOT NULL
          AND {art_clause}
        GROUP BY d.jurisdiction
        HAVING count(*) >= 3
        ORDER BY median_fine DESC
        LIMIT 15
    """, art_params)

    return {
        r[0]: {"median_fine": int(r[1]), "cases": r[2]}
        for r in cur.fetchall()
    }


# ── Step 4+5: Cap and adjust ─────────────────────────────────────────────────

def _weighted_percentile(values: list[float], weights: list[float],
                         percentile: float) -> float:
    """Compute weighted percentile. percentile in [0, 1]."""
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights))
    vals, wts = zip(*pairs)
    cum_weight = []
    total = sum(wts)
    running = 0.0
    for w in wts:
        running += w
        cum_weight.append((running - w / 2) / total)  # midpoint method
    for i, cw in enumerate(cum_weight):
        if cw >= percentile:
            return vals[i]
    return vals[-1]


def calculate_fine_range(precedents: list[Precedent],
                         starting_point: dict,
                         params: SimulationInput) -> dict[str, float]:
    """Calculate estimated fine range from precedents using weighted percentiles."""
    scored = [(p.fine_amount, p.similarity_score)
              for p in precedents if p.fine_amount > 0]

    if len(scored) >= 5:
        fines = [s[0] for s in scored]
        weights = [max(s[1], 0.01) for s in scored]  # floor to avoid zero
        p25 = _weighted_percentile(fines, weights, 0.25)
        median = _weighted_percentile(fines, weights, 0.50)
        p75 = _weighted_percentile(fines, weights, 0.75)
        fine_min = min(fines)
        fine_max = max(fines)
    elif scored:
        fines = [s[0] for s in scored]
        median = statistics.median(fines)
        p25 = min(fines)
        p75 = max(fines)
        fine_min = min(fines)
        fine_max = max(fines)
    else:
        return {
            "min": starting_point["starting_low"],
            "percentile_25": starting_point["starting_low"],
            "median": starting_point["starting_mid"],
            "percentile_75": starting_point["starting_high"],
            "max": starting_point["starting_high"],
            "precedent_count": 0,
            "note": "No matching precedents found; range based on EDPB methodology only",
        }

    # Apply factor adjustments
    adjustment = 1.0
    if params.intentional:
        adjustment *= 1.3
    if params.prior_violations:
        adjustment *= 1.25
    if params.cooperated:
        adjustment *= 0.85
    if params.notified_voluntarily:
        adjustment *= 0.90
    if params.corrective_measures:
        adjustment *= 0.90
    if params.data_categories in ("health", "biometric", "genetic", "children"):
        adjustment *= 1.2

    # Number of articles: mild aggravating (each extra article +5%, capped at +25%)
    n_articles = len(params.articles_violated)
    if n_articles > 1:
        adjustment *= min(1.0 + 0.05 * (n_articles - 1), 1.25)

    # Cap at legal maximum
    legal_max = starting_point["legal_max"]

    return {
        "min": int(min(fine_min * adjustment, legal_max)),
        "percentile_25": int(min(p25 * adjustment, legal_max)),
        "median": int(min(median * adjustment, legal_max)),
        "percentile_75": int(min(p75 * adjustment, legal_max)),
        "max": int(min(fine_max * adjustment, legal_max)),
        "precedent_count": len(scored),
        "adjustment_factor": round(adjustment, 2),
    }


# ── Main simulation function ─────────────────────────────────────────────────

def simulate_fine(conn: psycopg.Connection, params: SimulationInput) -> SimulationResult:
    """Run the full 5-step EDPB prosecution simulation."""

    # Step 1
    category = categorize_violation(params.articles_violated)

    # Step 2
    starting_point = calculate_starting_point(category, params.turnover_eur)

    # Step 3
    precedents = find_precedents(conn, params)
    factor_impacts = analyze_factor_impacts(conn, params)
    dpa_comparison = get_dpa_comparison(conn, params.articles_violated)

    # Steps 4+5
    estimated_range = calculate_fine_range(precedents, starting_point, params)

    # Build methodology explanation
    aggravating = []
    mitigating = []
    if params.intentional:
        aggravating.append("Intentional violation (+30%)")
    if params.prior_violations:
        aggravating.append("Prior violations (+25%)")
    if params.data_categories in ("health", "biometric", "genetic", "children"):
        aggravating.append(f"Sensitive data: {params.data_categories} (+20%)")
    if params.data_subjects_affected and params.data_subjects_affected > 10000:
        aggravating.append(f"{params.data_subjects_affected:,} data subjects affected")
    if params.cooperated:
        mitigating.append("Cooperated with DPA (-15%)")
    if params.notified_voluntarily:
        mitigating.append("Voluntarily notified (-10%)")
    if params.corrective_measures:
        mitigating.append("Took corrective measures (-10%)")

    methodology = {
        "step1_category": f"Articles {', '.join(params.articles_violated)}, "
                          f"severity: {category['severity']} ({category['tier']})",
        "step2_starting_point": {
            "legal_max": starting_point["legal_max"],
            "range": f"EUR {starting_point['starting_low']:,} - {starting_point['starting_high']:,}",
        },
        "step3_factors": {
            "aggravating": aggravating,
            "mitigating": mitigating,
        },
        "step4_legal_max": f"EUR {starting_point['legal_max']:,}",
        "step5_precedent_range": f"Based on {len(precedents)} matching cases",
    }

    # Confidence indicator
    n_prec = estimated_range.get("precedent_count", 0)
    p25 = estimated_range.get("percentile_25", 0)
    p75 = estimated_range.get("percentile_75", 0)
    if p25 and p75 and p25 > 0:
        iqr_ratio = p75 / p25
        # IQR width in orders of magnitude
        iqr_oom = math.log10(max(iqr_ratio, 1.01))
    else:
        iqr_ratio = 999
        iqr_oom = 7.0

    if n_prec >= 20 and iqr_oom < 1.5:
        conf_level, conf_score = "high", 85
    elif n_prec >= 10 and iqr_oom < 2.0:
        conf_level, conf_score = "medium", 60
    elif n_prec >= 5:
        conf_level, conf_score = "low", 35
    else:
        conf_level, conf_score = "very_low", 15

    confidence = {
        "level": conf_level,
        "score": conf_score,
        "precedent_count": n_prec,
        "iqr_ratio": round(iqr_ratio, 1),
        "iqr_orders_of_magnitude": round(iqr_oom, 2),
        "explanation": (
            f"{n_prec} matching precedents, IQR spans {iqr_oom:.1f} orders of magnitude. "
            f"{'Narrow range = more reliable.' if iqr_oom < 1.5 else 'Wide range = high uncertainty.'}"
        ),
    }

    return SimulationResult(
        estimated_range=estimated_range,
        methodology=methodology,
        precedents=precedents[:10],
        factor_impacts=factor_impacts,
        dpa_comparison=dpa_comparison,
        confidence=confidence,
    )
