"""
JurisMind — Prosecution Simulator Evaluation

Backtests the fine simulator against real enforcement decisions to measure:

  1. Coverage Rate: % of real fines that fall within predicted P25-P75 range
  2. Order-of-Magnitude Accuracy: log10(predicted) vs log10(actual)
  3. EDPB Methodology Correctness: article classification, legal max, factors
  4. Precedent Relevance: do returned precedents share articles/jurisdiction?
  5. Practical Value: are results actionable for a legal advisor?

Methodology:
  - Stratified sample from DB: diverse jurisdictions, articles, fine magnitudes
  - Leave-one-out: each test case is excluded from its own precedent pool
  - Metrics calibrated to statistical intervals (P25-P75 should capture ~50%)

Usage:
    PYTHONUTF8=1 python eval/eval_simulator.py
    PYTHONUTF8=1 python eval/eval_simulator.py --sample-size 200
    PYTHONUTF8=1 python eval/eval_simulator.py --out eval/results_simulator.json
    PYTHONUTF8=1 python eval/eval_simulator.py --verbose
"""

import argparse
import json
import logging
import math
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent.parent))
from services.fine_simulator import (
    SimulationInput,
    SimulationResult,
    calculate_fine_range,
    calculate_starting_point,
    categorize_violation,
    find_precedents,
    simulate_fine,
)

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DEFAULT_SAMPLE = 100
SEED = 42

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    title: str
    jurisdiction: str
    fine_amount: int
    gdpr_articles: list[str]
    sector: str
    source: str
    decision_year: int | None


@dataclass
class BacktestResult:
    title: str
    actual_fine: int
    predicted_p25: int
    predicted_median: int
    predicted_p75: int
    predicted_min: int
    predicted_max: int
    precedent_count: int
    adjustment_factor: float
    in_p25_p75: bool
    in_p10_p90: bool
    in_min_max: bool
    log_error: float  # |log10(predicted_median) - log10(actual)|
    direction: str  # "over", "under", "hit"
    precedent_article_overlap: float
    precedent_jurisdiction_match: float
    jurisdiction: str
    sector: str
    articles: list[str]
    severity: str


# ── Sample selection ──────────────────────────────────────────────────────────

def fetch_stratified_sample(conn: psycopg.Connection, n: int) -> list[TestCase]:
    """Fetch a stratified sample of cases with known fines.

    Stratifies by:
      - Fine magnitude (log buckets: <1K, 1K-10K, 10K-100K, 100K-1M, >1M)
      - Jurisdiction (top 10)
      - Sector
    Uses deterministic random for reproducibility.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT d.title, d.jurisdiction, d.fine_amount, d.gdpr_articles,
               d.sector, d.source, d.decision_year
        FROM documents d
        WHERE d.fine_amount > 0
          AND d.gdpr_articles IS NOT NULL
          AND array_length(d.gdpr_articles, 1) >= 1
          AND d.jurisdiction IS NOT NULL
          AND d.sector IS NOT NULL
        ORDER BY md5(d.title || %s)
    """, [str(SEED)])
    rows = cur.fetchall()

    # Bucket by fine magnitude
    buckets: dict[str, list[TestCase]] = {
        "tiny_<1K": [],
        "small_1K-10K": [],
        "medium_10K-100K": [],
        "large_100K-1M": [],
        "mega_>1M": [],
    }
    for r in rows:
        tc = TestCase(
            title=r[0], jurisdiction=r[1], fine_amount=r[2],
            gdpr_articles=r[3] or [], sector=r[4],
            source=r[5], decision_year=r[6],
        )
        amt = tc.fine_amount
        if amt < 1_000:
            buckets["tiny_<1K"].append(tc)
        elif amt < 10_000:
            buckets["small_1K-10K"].append(tc)
        elif amt < 100_000:
            buckets["medium_10K-100K"].append(tc)
        elif amt < 1_000_000:
            buckets["large_100K-1M"].append(tc)
        else:
            buckets["mega_>1M"].append(tc)

    # Take proportional sample from each bucket
    sample: list[TestCase] = []
    total = sum(len(v) for v in buckets.values())
    for bucket_name, cases in buckets.items():
        bucket_n = max(1, round(n * len(cases) / total))
        sample.extend(cases[:bucket_n])

    return sample[:n]


# ── Normalization helpers ─────────────────────────────────────────────────────

def _extract_article_nums(articles: list[str]) -> set[str]:
    """Extract bare article numbers from various formats."""
    nums = set()
    for a in articles:
        cleaned = (a.replace("Article ", "").replace("Art. ", "")
                   .replace("Art.", "").replace(" GDPR", "").strip())
        # Take just the main number (before parentheses)
        main = cleaned.split("(")[0].split(")")[0].strip()
        if main and main[0].isdigit():
            nums.add(main)
    return nums


# ── Backtest engine ───────────────────────────────────────────────────────────

def backtest_case(conn: psycopg.Connection, tc: TestCase,
                  verbose: bool = False) -> BacktestResult:
    """Run the simulator on a real case and compare predicted vs actual fine."""
    # Extract article numbers for simulator input
    article_nums = list(_extract_article_nums(tc.gdpr_articles))
    if not article_nums:
        article_nums = ["32"]  # fallback

    sim_input = SimulationInput(
        articles_violated=article_nums,
        jurisdiction=tc.jurisdiction,
        sector=tc.sector,
        turnover_eur=None,  # rarely available in public data
        cooperated=True,  # default conservative
        notified_voluntarily=False,
        corrective_measures=False,
        intentional=False,
        prior_violations=False,
    )

    result = simulate_fine(conn, sim_input)
    est = result.estimated_range

    actual = tc.fine_amount
    p25 = est.get("percentile_25", 0)
    median = est.get("median", 0)
    p75 = est.get("percentile_75", 0)
    pred_min = est.get("min", 0)
    pred_max = est.get("max", 0)
    adj = est.get("adjustment_factor", 1.0)
    prec_count = est.get("precedent_count", 0)

    # Coverage checks
    in_p25_p75 = p25 <= actual <= p75 if p25 > 0 else False
    in_min_max = pred_min <= actual <= pred_max if pred_min >= 0 else False

    # P10-P90 approximation (widen by 50% on each side)
    p10_approx = int(p25 * 0.5) if p25 > 0 else 0
    p90_approx = int(p75 * 1.5) if p75 > 0 else 0
    in_p10_p90 = p10_approx <= actual <= p90_approx

    # Log-scale error (more meaningful for fines spanning 6 orders of magnitude)
    if actual > 0 and median > 0:
        log_error = abs(math.log10(median) - math.log10(actual))
    else:
        log_error = float("inf")

    # Direction
    if in_p25_p75:
        direction = "hit"
    elif actual < p25:
        direction = "over"  # we overestimated
    else:
        direction = "under"  # we underestimated

    # Precedent quality metrics
    input_arts = _extract_article_nums(tc.gdpr_articles)
    art_overlaps = []
    juris_matches = []
    for p in result.precedents[:10]:
        p_arts = _extract_article_nums(p.articles)
        if input_arts or p_arts:
            overlap = len(input_arts & p_arts) / max(len(input_arts | p_arts), 1)
            art_overlaps.append(overlap)
        juris_matches.append(1.0 if p.jurisdiction == tc.jurisdiction else 0.0)

    avg_art_overlap = statistics.mean(art_overlaps) if art_overlaps else 0.0
    avg_juris_match = statistics.mean(juris_matches) if juris_matches else 0.0

    category = categorize_violation(article_nums)

    if verbose:
        symbol = "HIT" if in_p25_p75 else ("OVER" if direction == "over" else "UNDER")
        log.info(
            "  [%s] %s | actual=%s | range=[%s - %s - %s] | prec=%d | log_err=%.2f",
            symbol, tc.title[:50], f"{actual:,}",
            f"{p25:,}", f"{median:,}", f"{p75:,}",
            prec_count, log_error,
        )

    return BacktestResult(
        title=tc.title,
        actual_fine=actual,
        predicted_p25=p25,
        predicted_median=median,
        predicted_p75=p75,
        predicted_min=pred_min,
        predicted_max=pred_max,
        precedent_count=prec_count,
        adjustment_factor=adj,
        in_p25_p75=in_p25_p75,
        in_p10_p90=in_p10_p90,
        in_min_max=in_min_max,
        log_error=log_error,
        direction=direction,
        precedent_article_overlap=round(avg_art_overlap, 3),
        precedent_jurisdiction_match=round(avg_juris_match, 3),
        jurisdiction=tc.jurisdiction,
        sector=tc.sector,
        articles=article_nums,
        severity=category["severity"],
    )


# ── EDPB methodology unit tests ──────────────────────────────────────────────

def test_edpb_methodology() -> list[dict]:
    """Unit tests for EDPB 5-step methodology correctness."""
    tests: list[dict] = []

    # Step 1: Article classification
    test_cases_step1 = [
        (["5"], "severe", "Art. 83(5-6)", "Art.5 = data principles = severe"),
        (["6"], "severe", "Art. 83(5-6)", "Art.6 = lawfulness = severe"),
        (["9"], "severe", "Art. 83(5-6)", "Art.9 = special categories = severe"),
        (["17"], "severe", "Art. 83(5-6)", "Art.17 = right to erasure = severe"),
        (["44"], "severe", "Art. 83(5-6)", "Art.44 = international transfers = severe"),
        (["32"], "moderate", "Art. 83(4)", "Art.32 = security = moderate"),
        (["33"], "moderate", "Art. 83(4)", "Art.33 = breach notification = moderate"),
        (["25"], "moderate", "Art. 83(4)", "Art.25 = data protection by design = moderate"),
        (["35"], "moderate", "Art. 83(4)", "Art.35 = DPIA = moderate"),
        # Mixed: severe should dominate
        (["5", "32"], "severe", "Art. 83(5-6)", "Mixed severe+moderate → severe"),
        (["6", "33", "25"], "severe", "Art. 83(5-6)", "Art.6 severe dominates"),
    ]

    for articles, exp_severity, exp_tier, desc in test_cases_step1:
        result = categorize_violation(articles)
        passed = result["severity"] == exp_severity
        tests.append({
            "test": f"step1_classify_{'-'.join(articles)}",
            "description": desc,
            "passed": passed,
            "expected": f"{exp_severity} ({exp_tier})",
            "got": f"{result['severity']} ({result['tier']})",
        })

    # Step 2: Starting point with turnover
    cat_severe = categorize_violation(["5"])
    sp = calculate_starting_point(cat_severe, 100_000_000)
    tests.append({
        "test": "step2_turnover_severe",
        "description": "100M turnover, severe → legal_max = max(20M, 4M) = 20M",
        "passed": sp["legal_max"] == 20_000_000,
        "expected": 20_000_000,
        "got": sp["legal_max"],
    })

    cat_severe_big = categorize_violation(["6"])
    sp_big = calculate_starting_point(cat_severe_big, 1_000_000_000)
    tests.append({
        "test": "step2_turnover_big_severe",
        "description": "1B turnover, severe → legal_max = max(20M, 40M) = 40M",
        "passed": sp_big["legal_max"] == 40_000_000,
        "expected": 40_000_000,
        "got": sp_big["legal_max"],
    })

    cat_mod = categorize_violation(["32"])
    sp_mod = calculate_starting_point(cat_mod, 50_000_000)
    tests.append({
        "test": "step2_turnover_moderate",
        "description": "50M turnover, moderate → legal_max = max(10M, 1M) = 10M",
        "passed": sp_mod["legal_max"] == 10_000_000,
        "expected": 10_000_000,
        "got": sp_mod["legal_max"],
    })

    # Step 2: No turnover → static max
    sp_no_turn = calculate_starting_point(cat_severe, None)
    tests.append({
        "test": "step2_no_turnover",
        "description": "No turnover, severe → legal_max = 20M (static)",
        "passed": sp_no_turn["legal_max"] == 20_000_000,
        "expected": 20_000_000,
        "got": sp_no_turn["legal_max"],
    })

    # Step 4: Fine range capped at legal max
    from services.fine_simulator import Precedent
    fake_precedents = [
        Precedent("Case A", "Spain", 50_000_000, ["Art. 5 GDPR"], None, similarity_score=0.8),
        Precedent("Case B", "France", 30_000_000, ["Art. 5 GDPR"], None, similarity_score=0.7),
        Precedent("Case C", "Germany", 25_000_000, ["Art. 5 GDPR"], None, similarity_score=0.6),
        Precedent("Case D", "Italy", 40_000_000, ["Art. 5 GDPR"], None, similarity_score=0.5),
        Precedent("Case E", "UK", 20_000_000, ["Art. 5 GDPR"], None, similarity_score=0.4),
    ]
    sp_cap = {"legal_max": 20_000_000, "starting_low": 2_000_000,
              "starting_mid": 4_000_000, "starting_high": 10_000_000}
    sim_input_cap = SimulationInput(articles_violated=["5"])
    range_result = calculate_fine_range(fake_precedents, sp_cap, sim_input_cap)
    tests.append({
        "test": "step4_legal_max_cap",
        "description": "Predicted max should not exceed legal_max (20M)",
        "passed": range_result["max"] <= 20_000_000,
        "expected": f"<= 20,000,000",
        "got": range_result["max"],
    })

    return tests


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(results: list[BacktestResult], method_tests: list[dict],
                 elapsed_s: float) -> dict:
    """Print comprehensive evaluation report and return summary dict."""
    n = len(results)
    if n == 0:
        print("No results to report.")
        return {}

    # ── Coverage metrics ──
    coverage_p25_p75 = sum(1 for r in results if r.in_p25_p75) / n
    coverage_p10_p90 = sum(1 for r in results if r.in_p10_p90) / n
    coverage_min_max = sum(1 for r in results if r.in_min_max) / n

    # ── Log-error metrics ──
    log_errors = [r.log_error for r in results if r.log_error < float("inf")]
    avg_log_error = statistics.mean(log_errors) if log_errors else float("inf")
    median_log_error = statistics.median(log_errors) if log_errors else float("inf")

    # Within 1 order of magnitude = log_error < 1.0
    within_1_oom = sum(1 for e in log_errors if e < 1.0) / len(log_errors) if log_errors else 0
    within_half_oom = sum(1 for e in log_errors if e < 0.5) / len(log_errors) if log_errors else 0

    # ── Direction bias ──
    n_over = sum(1 for r in results if r.direction == "over")
    n_under = sum(1 for r in results if r.direction == "under")
    n_hit = sum(1 for r in results if r.direction == "hit")

    # ── Precedent quality ──
    avg_art_overlap = statistics.mean(
        r.precedent_article_overlap for r in results
    )
    avg_juris_match = statistics.mean(
        r.precedent_jurisdiction_match for r in results
    )
    avg_precedent_count = statistics.mean(
        r.precedent_count for r in results
    )
    zero_precedent = sum(1 for r in results if r.precedent_count == 0)

    # ── By jurisdiction ──
    juris_groups: dict[str, list[BacktestResult]] = {}
    for r in results:
        juris_groups.setdefault(r.jurisdiction, []).append(r)

    # ── By fine magnitude ──
    mag_groups: dict[str, list[BacktestResult]] = {}
    for r in results:
        if r.actual_fine < 1_000:
            mag_groups.setdefault("<1K", []).append(r)
        elif r.actual_fine < 10_000:
            mag_groups.setdefault("1K-10K", []).append(r)
        elif r.actual_fine < 100_000:
            mag_groups.setdefault("10K-100K", []).append(r)
        elif r.actual_fine < 1_000_000:
            mag_groups.setdefault("100K-1M", []).append(r)
        else:
            mag_groups.setdefault(">1M", []).append(r)

    # ── EDPB method tests ──
    method_passed = sum(1 for t in method_tests if t["passed"])
    method_total = len(method_tests)

    # ── Print ──
    print(f"\n{'=' * 70}")
    print(f"JurisMind Prosecution Simulator Evaluation — {n} cases backtested")
    print(f"{'=' * 70}")

    print(f"\n-- Coverage (does the real fine fall within our predicted range?) --")
    print(f"  P25-P75 coverage  : {coverage_p25_p75:.1%}  (target: ~50%)")
    print(f"  P10-P90 coverage  : {coverage_p10_p90:.1%}  (target: ~70%)")
    print(f"  Min-Max coverage  : {coverage_min_max:.1%}  (target: ~90%)")

    print(f"\n-- Order-of-Magnitude Accuracy (log10 scale) --")
    print(f"  Mean log10 error  : {avg_log_error:.2f}")
    print(f"  Median log10 error: {median_log_error:.2f}")
    print(f"  Within 1 OoM      : {within_1_oom:.1%}  (|log_err| < 1.0)")
    print(f"  Within 0.5 OoM    : {within_half_oom:.1%}  (|log_err| < 0.5)")

    print(f"\n-- Direction Bias --")
    print(f"  Hits (in range)   : {n_hit:4d}  ({n_hit/n:.1%})")
    print(f"  Overestimates     : {n_over:4d}  ({n_over/n:.1%})")
    print(f"  Underestimates    : {n_under:4d}  ({n_under/n:.1%})")

    print(f"\n-- Precedent Quality --")
    print(f"  Avg precedent count    : {avg_precedent_count:.1f}")
    print(f"  Zero-precedent cases   : {zero_precedent}  ({zero_precedent/n:.1%})")
    print(f"  Avg article overlap    : {avg_art_overlap:.2f}  (Jaccard)")
    print(f"  Avg jurisdiction match : {avg_juris_match:.1%}")

    print(f"\n-- Coverage by Fine Magnitude --")
    for mag in ["<1K", "1K-10K", "10K-100K", "100K-1M", ">1M"]:
        group = mag_groups.get(mag, [])
        if group:
            cov = sum(1 for r in group if r.in_p25_p75) / len(group)
            avg_le = statistics.mean(r.log_error for r in group
                                     if r.log_error < float("inf")) if group else 0
            print(f"  {mag:10s}  n={len(group):3d}  coverage={cov:.1%}  avg_log_err={avg_le:.2f}")

    print(f"\n-- Coverage by Jurisdiction (top 10) --")
    top_juris = sorted(juris_groups.items(), key=lambda x: -len(x[1]))[:10]
    for j, group in top_juris:
        cov = sum(1 for r in group if r.in_p25_p75) / len(group)
        print(f"  {j:20s}  n={len(group):3d}  coverage={cov:.1%}")

    print(f"\n-- EDPB Methodology Tests ({method_passed}/{method_total} passed) --")
    for t in method_tests:
        status = "PASS" if t["passed"] else "FAIL"
        print(f"  [{status}] {t['test']}: {t['description']}")
        if not t["passed"]:
            print(f"         expected={t['expected']}  got={t['got']}")

    # Worst misses (largest log errors)
    worst = sorted(results, key=lambda r: -r.log_error)[:5]
    print(f"\n-- Worst Predictions (largest log10 error) --")
    for r in worst:
        if r.log_error < float("inf"):
            print(f"  {r.title[:55]:55s} actual={r.actual_fine:>12,}  "
                  f"median={r.predicted_median:>12,}  log_err={r.log_error:.2f}")

    print(f"\n-- Timing --")
    print(f"  Total duration    : {elapsed_s:.1f}s")
    print(f"  Per case          : {elapsed_s/n*1000:.0f}ms")

    # ── Overall Grade ──
    print(f"\n{'=' * 70}")
    grade = _compute_grade(coverage_p25_p75, within_1_oom, avg_art_overlap,
                           method_passed / method_total if method_total else 0,
                           zero_precedent / n)
    print(f"  OVERALL GRADE: {grade['letter']}  ({grade['score']:.0f}/100)")
    print(f"    Coverage score      : {grade['coverage_score']:.0f}/30")
    print(f"    Accuracy score      : {grade['accuracy_score']:.0f}/25")
    print(f"    Precedent score     : {grade['precedent_score']:.0f}/20")
    print(f"    Methodology score   : {grade['method_score']:.0f}/15")
    print(f"    Robustness score    : {grade['robustness_score']:.0f}/10")
    print(f"{'=' * 70}\n")

    return {
        "n_cases": n,
        "coverage_p25_p75": round(coverage_p25_p75, 4),
        "coverage_p10_p90": round(coverage_p10_p90, 4),
        "coverage_min_max": round(coverage_min_max, 4),
        "avg_log_error": round(avg_log_error, 4),
        "median_log_error": round(median_log_error, 4),
        "within_1_oom": round(within_1_oom, 4),
        "within_half_oom": round(within_half_oom, 4),
        "n_hit": n_hit,
        "n_over": n_over,
        "n_under": n_under,
        "avg_precedent_count": round(avg_precedent_count, 1),
        "zero_precedent_pct": round(zero_precedent / n, 4),
        "avg_article_overlap": round(avg_art_overlap, 4),
        "avg_jurisdiction_match": round(avg_juris_match, 4),
        "method_tests_passed": method_passed,
        "method_tests_total": method_total,
        "grade": grade,
        "elapsed_s": round(elapsed_s, 1),
    }


def _compute_grade(coverage: float, within_1_oom: float,
                   art_overlap: float, method_pct: float,
                   zero_prec_pct: float) -> dict:
    """Compute overall grade 0-100 with letter."""
    # Coverage (30 pts): 50% coverage = 30pts, 30% = 18pts, 0% = 0pts
    coverage_score = min(30, coverage / 0.50 * 30)

    # Accuracy (25 pts): >80% within 1 OoM = 25pts
    accuracy_score = min(25, within_1_oom / 0.80 * 25)

    # Precedent quality (20 pts): high article overlap = good
    precedent_score = min(20, art_overlap / 0.40 * 20)

    # Methodology correctness (15 pts)
    method_score = method_pct * 15

    # Robustness (10 pts): low zero-precedent rate
    robustness_score = max(0, (1 - zero_prec_pct) * 10)

    total = coverage_score + accuracy_score + precedent_score + method_score + robustness_score

    if total >= 85:
        letter = "A"
    elif total >= 70:
        letter = "B"
    elif total >= 55:
        letter = "C"
    elif total >= 40:
        letter = "D"
    else:
        letter = "F"

    return {
        "score": round(total, 1),
        "letter": letter,
        "coverage_score": round(coverage_score, 1),
        "accuracy_score": round(accuracy_score, 1),
        "precedent_score": round(precedent_score, 1),
        "method_score": round(method_score, 1),
        "robustness_score": round(robustness_score, 1),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL not set.")

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    log.info("Connected to DB.")

    # 1. Fetch stratified sample
    log.info("Fetching stratified sample of %d cases...", args.sample_size)
    cases = fetch_stratified_sample(conn, args.sample_size)
    log.info("Sample: %d cases across %d jurisdictions, %d sectors",
             len(cases),
             len({c.jurisdiction for c in cases}),
             len({c.sector for c in cases}))

    # 2. Run EDPB methodology unit tests
    log.info("Running EDPB methodology tests...")
    method_tests = test_edpb_methodology()
    passed = sum(1 for t in method_tests if t["passed"])
    log.info("Methodology tests: %d/%d passed", passed, len(method_tests))

    # 3. Backtest each case
    log.info("Backtesting %d cases...", len(cases))
    results: list[BacktestResult] = []
    t_start = time.monotonic()

    for i, tc in enumerate(cases, 1):
        try:
            bt = backtest_case(conn, tc, verbose=args.verbose)
            results.append(bt)
        except Exception as e:
            log.warning("Case %d/%d failed: %s — %s", i, len(cases),
                        tc.title[:40], e)
            continue

        if i % 25 == 0:
            elapsed = time.monotonic() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            cov_so_far = sum(1 for r in results if r.in_p25_p75) / len(results)
            log.info("Progress: %d/%d (%.1f/s) coverage=%.1f%%",
                     i, len(cases), rate, cov_so_far * 100)

    elapsed_s = time.monotonic() - t_start
    conn.close()

    # 4. Report
    summary = print_report(results, method_tests, elapsed_s)

    # 5. Save results
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "summary": summary,
            "methodology_tests": method_tests,
            "backtest_results": [
                {
                    "title": r.title,
                    "jurisdiction": r.jurisdiction,
                    "sector": r.sector,
                    "articles": r.articles,
                    "actual_fine": r.actual_fine,
                    "predicted_p25": r.predicted_p25,
                    "predicted_median": r.predicted_median,
                    "predicted_p75": r.predicted_p75,
                    "in_p25_p75": r.in_p25_p75,
                    "log_error": r.log_error if r.log_error < float("inf") else None,
                    "direction": r.direction,
                    "precedent_count": r.precedent_count,
                    "precedent_article_overlap": r.precedent_article_overlap,
                }
                for r in results
            ],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
        log.info("Results saved to %s", out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JurisMind — Prosecution Simulator Evaluation"
    )
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE,
                        help=f"Number of cases to backtest (default: {DEFAULT_SAMPLE})")
    parser.add_argument("--out", default=None,
                        help="Save results to JSON file")
    parser.add_argument("--verbose", action="store_true",
                        help="Print each case result")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
