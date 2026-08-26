"""Data precision evaluation: check if agent's response facts match the DB.

Compares fine amounts, GDPR articles, jurisdiction, and year mentioned
in the agent's response against the actual document metadata.

Usage:
    PYTHONUTF8=1 python eval/eval_precision.py --results eval/results_recalldb_rewrites.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Extraction patterns ──────────────────────────────────────────────────────

def extract_fines(text: str) -> list[int]:
    """Extract fine amounts from agent response."""
    fines: list[int] = []
    # EUR 180,000 / €180,000 / EUR 180000 / 180.000 EUR
    for m in re.finditer(
        r'(?:EUR|€)\s*([\d,.\s]+)|'
        r'([\d,.\s]+)\s*(?:EUR|€|euros?)',
        text, re.IGNORECASE,
    ):
        raw = (m.group(1) or m.group(2)).strip()
        # Normalize: remove spaces, handle comma/dot as thousands sep
        raw = raw.replace(" ", "")
        # Detect format: 180,000 vs 180.000 vs 180000
        if "," in raw and "." in raw:
            # 1,234,567.89 or 1.234.567,89
            if raw.rindex(",") > raw.rindex("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            # Could be 180,000 (en) or 180,50 (decimal)
            parts = raw.split(",")
            if len(parts[-1]) == 3:
                raw = raw.replace(",", "")  # thousands separator
            else:
                raw = raw.replace(",", ".")  # decimal
        elif "." in raw:
            parts = raw.split(".")
            if len(parts[-1]) == 3:
                raw = raw.replace(".", "")  # thousands separator (de/es)
        try:
            val = int(float(raw))
            if val >= 50:  # filter out noise like "EUR 0"
                fines.append(val)
        except ValueError:
            pass
    return fines


def extract_articles(text: str) -> list[str]:
    """Extract GDPR article numbers from agent response."""
    arts: set[str] = set()
    for m in re.finditer(r'Art(?:icle)?\.?\s*(\d+)(?:\s*\((\d+)\))?', text, re.IGNORECASE):
        num = m.group(1)
        sub = m.group(2)
        arts.add(num)
        if sub:
            arts.add(f"{num}({sub})")
    return sorted(arts)


def extract_years(text: str) -> list[int]:
    """Extract decision years from agent response."""
    years: set[int] = set()
    for m in re.finditer(r'\b(20[12]\d)\b', text):
        years.add(int(m.group(1)))
    return sorted(years)


def extract_jurisdictions(text: str) -> list[str]:
    """Extract country/jurisdiction names from agent response."""
    countries = [
        "France", "Germany", "Spain", "Italy", "Belgium", "Netherlands",
        "Austria", "Greece", "Poland", "Ireland", "Sweden", "Denmark",
        "Finland", "Norway", "Portugal", "Hungary", "Romania", "Czech Republic",
        "Slovakia", "Slovenia", "Croatia", "Bulgaria", "Latvia", "Lithuania",
        "Estonia", "Malta", "Cyprus", "Luxembourg", "Liechtenstein",
        "United Kingdom", "Iceland",
    ]
    found: list[str] = []
    text_lower = text.lower()
    for c in countries:
        if c.lower() in text_lower:
            found.append(c)
    return found


# ── DB lookup ─────────────────────────────────────────────────────────────────

def get_doc_metadata(conn: psycopg.Connection, title: str) -> dict | None:
    """Look up document metadata by title (exact or fuzzy)."""
    cur = conn.cursor()

    # Try exact match first
    cur.execute("""
        SELECT title, fine_amount, gdpr_articles, jurisdiction,
               decision_date, decision_year, authority, case_number
        FROM documents
        WHERE title = %s
        LIMIT 1
    """, (title,))
    row = cur.fetchone()

    if not row:
        # Try ILIKE
        cur.execute("""
            SELECT title, fine_amount, gdpr_articles, jurisdiction,
                   decision_date, decision_year, authority, case_number
            FROM documents
            WHERE title ILIKE %s
            LIMIT 1
        """, (f"%{title[:60]}%",))
        row = cur.fetchone()

    if not row:
        return None

    # Extract article numbers from gdpr_articles array
    art_nums: list[str] = []
    if row[2]:
        for a in row[2]:
            for m in re.finditer(r'(\d+)(?:\((\d+)\))?', a):
                art_nums.append(m.group(1))
                if m.group(2):
                    art_nums.append(f"{m.group(1)}({m.group(2)})")

    year = None
    if row[5]:
        year = int(row[5])
    elif row[4]:
        year = row[4].year if hasattr(row[4], "year") else None

    return {
        "title": row[0],
        "fine_amount": row[1],
        "gdpr_articles": art_nums,
        "jurisdiction": row[3],
        "year": year,
        "authority": row[6],
        "case_number": row[7],
    }


# ── Precision scoring ────────────────────────────────────────────────────────

def check_fine_precision(response_fines: list[int], db_fine: int | None) -> dict:
    """Check if the agent reported the correct fine amount."""
    if db_fine is None:
        return {"status": "no_ground_truth", "correct": None}
    if not response_fines:
        return {"status": "not_mentioned", "correct": None}

    # Check if any extracted fine matches (within 5% tolerance for rounding)
    for rf in response_fines:
        ratio = rf / db_fine if db_fine > 0 else 0
        if 0.95 <= ratio <= 1.05:
            return {"status": "correct", "correct": True,
                    "extracted": rf, "actual": db_fine}

    return {"status": "incorrect", "correct": False,
            "extracted": response_fines[0], "actual": db_fine}


def check_articles_precision(
    response_articles: list[str], db_articles: list[str],
) -> dict:
    """Check if agent mentioned the correct GDPR articles."""
    if not db_articles:
        return {"status": "no_ground_truth", "correct": None}
    if not response_articles:
        return {"status": "not_mentioned", "correct": None}

    # Base article numbers only (strip sub-paragraphs for comparison)
    db_base = {a.split("(")[0] for a in db_articles}
    resp_base = {a.split("(")[0] for a in response_articles}

    overlap = db_base & resp_base
    if not overlap:
        return {"status": "incorrect", "correct": False,
                "extracted": response_articles, "actual": db_articles}

    precision = len(overlap) / len(resp_base) if resp_base else 0
    recall = len(overlap) / len(db_base) if db_base else 0

    return {
        "status": "partial" if recall < 1.0 else "correct",
        "correct": recall >= 0.5,  # at least half of articles mentioned
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "overlap": sorted(overlap),
        "extracted": response_articles,
        "actual": db_articles,
    }


def check_jurisdiction_precision(
    response_jurisdictions: list[str], db_jurisdiction: str | None,
) -> dict:
    """Check if agent mentioned the correct jurisdiction."""
    if not db_jurisdiction:
        return {"status": "no_ground_truth", "correct": None}
    if not response_jurisdictions:
        return {"status": "not_mentioned", "correct": None}

    db_lower = db_jurisdiction.lower()
    for rj in response_jurisdictions:
        if rj.lower() in db_lower or db_lower in rj.lower():
            return {"status": "correct", "correct": True}

    return {"status": "incorrect", "correct": False,
            "extracted": response_jurisdictions, "actual": db_jurisdiction}


def check_year_precision(
    response_years: list[int], db_year: int | None,
) -> dict:
    """Check if agent mentioned the correct decision year."""
    if not db_year:
        return {"status": "no_ground_truth", "correct": None}
    if not response_years:
        return {"status": "not_mentioned", "correct": None}

    if db_year in response_years:
        return {"status": "correct", "correct": True}

    return {"status": "incorrect", "correct": False,
            "extracted": response_years, "actual": db_year}


# ── Main ──────────────────────────────────────────────────────────────────────

def evaluate_precision(
    results: list[dict], conn: psycopg.Connection,
) -> list[dict]:
    """Run data precision evaluation on agent results."""
    precision_results: list[dict] = []

    hits = [r for r in results
            if (r.get("hit_rate", {}).get(5, 0) > 0
                or r.get("hit_rate", {}).get("5", 0) > 0)
            and not r.get("_meta") and "error" not in r]

    log.info("Evaluating data precision on %d HITs", len(hits))

    for r in hits:
        qid = r["id"]
        response = r.get("response_preview", "")
        relevant_ids = r.get("relevant_ids", [])

        # Look up the relevant doc in DB
        doc_meta = None
        for rid in relevant_ids:
            doc_meta = get_doc_metadata(conn, rid)
            if doc_meta:
                break

        if not doc_meta:
            precision_results.append({
                "id": qid, "doc_found": False,
            })
            continue

        # Extract facts from response
        resp_fines = extract_fines(response)
        resp_articles = extract_articles(response)
        resp_years = extract_years(response)
        resp_jurisdictions = extract_jurisdictions(response)

        # Compare
        entry = {
            "id": qid,
            "doc_found": True,
            "doc_title": doc_meta["title"],
            "fine": check_fine_precision(resp_fines, doc_meta["fine_amount"]),
            "articles": check_articles_precision(resp_articles, doc_meta["gdpr_articles"]),
            "jurisdiction": check_jurisdiction_precision(resp_jurisdictions, doc_meta["jurisdiction"]),
            "year": check_year_precision(resp_years, doc_meta["year"]),
        }
        precision_results.append(entry)

    return precision_results


def print_precision_report(results: list[dict]) -> None:
    """Print aggregate precision metrics."""
    if not results:
        print("No results to evaluate.")
        return

    fields = ["fine", "articles", "jurisdiction", "year"]

    print(f"\n{'=' * 65}")
    print(f"Data Precision Evaluation — {len(results)} HITs analyzed")
    print("=" * 65)

    for field in fields:
        checked = [r for r in results if r.get(field, {}).get("correct") is not None]
        correct = [r for r in checked if r[field]["correct"]]
        not_mentioned = [r for r in results
                         if r.get(field, {}).get("status") == "not_mentioned"]
        no_gt = [r for r in results
                 if r.get(field, {}).get("status") == "no_ground_truth"]

        if checked:
            acc = len(correct) / len(checked)
            print(f"\n  {field.upper():15s}: {acc:.1%}  ({len(correct)}/{len(checked)} correct)")
        else:
            print(f"\n  {field.upper():15s}: N/A  (no data to compare)")

        if not_mentioned:
            print(f"    {'':15s}  {len(not_mentioned)} not mentioned in response")
        if no_gt:
            print(f"    {'':15s}  {len(no_gt)} no ground truth in DB")

    # Errors (incorrect)
    print(f"\n{'─' * 65}")
    print("Incorrect facts:")
    for r in results:
        errors = []
        for field in fields:
            info = r.get(field, {})
            if info.get("correct") is False:
                extracted = info.get("extracted", "?")
                actual = info.get("actual", "?")
                errors.append(f"{field}: got {extracted}, expected {actual}")
        if errors:
            print(f"  [{r['id']}] {'; '.join(errors)}")

    # Overall score
    all_checks = []
    for r in results:
        for field in fields:
            if r.get(field, {}).get("correct") is not None:
                all_checks.append(r[field]["correct"])

    if all_checks:
        overall = sum(all_checks) / len(all_checks)
        print(f"\n{'=' * 65}")
        print(f"OVERALL DATA PRECISION: {overall:.1%}  ({sum(all_checks)}/{len(all_checks)} facts correct)")
        print("=" * 65)


def main() -> None:
    parser = argparse.ArgumentParser(description="Data Precision Evaluation")
    parser.add_argument("--results", required=True, help="Path to eval results JSON")
    parser.add_argument("--output", default=None, help="Save precision results to JSON")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL not set")

    with open(args.results, encoding="utf-8") as f:
        results = json.load(f)

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    precision = evaluate_precision(results, conn)
    conn.close()

    print_precision_report(precision)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(precision, f, indent=2, ensure_ascii=False, default=str)
        log.info("Precision results saved to %s", out_path)


if __name__ == "__main__":
    main()
