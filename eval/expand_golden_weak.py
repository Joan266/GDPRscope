"""Expand golden set with more queries for weak categories.

Reuses build_golden_v5.py logic but only generates for categories
with HR@5 < 80%. Merges into golden_set_v5.json with new IDs.

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python eval/expand_golden_weak.py --parallel 5
"""

import argparse
import json
import logging
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from build_golden_v5 import (
    CATEGORY_PROMPTS,
    apply_quality_filters,
    build_cross_jurisdiction_pairs,
    build_multi_target_groups,
    deduplicate_queries,
    generate_single_query,
    get_client,
    sample_docs,
    sample_for_category,
    _generate_batch_parallel,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GS_PATH = Path("eval/golden_set_v5.json")
RESULTS_PATH = Path("eval/results_v6_bge_m3.json")
OUTPUT_PATH = Path("eval/golden_set_v6.json")

# Categories below this HR@5 threshold get expanded
HR_THRESHOLD = 0.80
# How many extra queries per weak category
EXTRA_PER_CATEGORY = 25


def find_weak_categories() -> dict[str, int]:
    """Find categories with HR@5 < threshold and compute how many to add."""
    with open(GS_PATH, encoding="utf-8") as f:
        gs = json.load(f)
    with open(RESULTS_PATH, encoding="utf-8") as f:
        results = json.load(f)

    results_map = {r["id"]: r for r in results}
    cats_total: Counter[str] = Counter()
    cats_hit: Counter[str] = Counter()

    for q in gs:
        cat = q["category"]
        cats_total[cat] += 1
        r = results_map.get(q["id"])
        if r and r.get("hit_rate", {}).get("5", 0) > 0:
            cats_hit[cat] += 1

    weak: dict[str, int] = {}
    for cat in sorted(cats_total.keys()):
        t = cats_total[cat]
        h = cats_hit.get(cat, 0)
        hr = h / t if t else 0
        if hr < HR_THRESHOLD:
            needed = min(EXTRA_PER_CATEGORY, max(10, t - h))
            weak[cat] = needed
            log.info("  WEAK %s: %d/%d = %.0f%% → +%d queries",
                     cat, h, t, hr * 100, needed)
        else:
            log.info("  OK   %s: %d/%d = %.0f%%", cat, h, t, hr * 100)

    return weak


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand golden set for weak categories")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Show plan without generating")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        log.error("OPENROUTER_API_KEY not set")
        sys.exit(1)

    log.info("=== Finding weak categories ===")
    weak_cats = find_weak_categories()

    if not weak_cats:
        log.info("No weak categories found (all >= %.0f%% HR@5)", HR_THRESHOLD * 100)
        return

    total_needed = sum(weak_cats.values())
    log.info("Total new queries to generate: %d across %d categories",
             total_needed, len(weak_cats))

    if args.dry_run:
        log.info("DRY RUN — exiting without generating")
        return

    random.seed(2026)
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    client = get_client()

    # Load existing golden set
    with open(GS_PATH, encoding="utf-8") as f:
        existing_gs = json.load(f)

    # Track used source IDs
    used_source_ids: set[str] = set()
    for q in existing_gs:
        for sid in q.get("relevant_source_ids", []):
            used_source_ids.add(sid)

    # Sample docs
    all_docs = sample_docs(conn)
    all_new_queries: list[dict] = []

    for cat, needed in weak_cats.items():
        log.info("--- Generating %d queries for '%s' ---", needed, cat)

        used_ids = set(used_source_ids) | {q.get("source_id", "") for q in all_new_queries}

        if cat == "cross_jurisdiction":
            pairs = build_cross_jurisdiction_pairs(all_docs, needed, used_ids)
            tasks = [(d1, cat, d2, None) for d1, d2 in pairs]
        elif cat == "multi_target":
            groups = build_multi_target_groups(all_docs, needed, used_ids)
            tasks = [(grp[0], cat, None, (grp, cdesc)) for grp, cdesc in groups]
        else:
            sampled = sample_for_category(all_docs, cat, needed, used_ids)
            tasks = [(doc, cat, None, None) for doc in sampled]

        batch_results, batch_errors = _generate_batch_parallel(
            client, tasks, needed, args.parallel,
        )
        for r in batch_results:
            all_new_queries.append(r)
            used_ids.add(r["source_id"])
        log.info("  %s: %d generated (%d errors)", cat, len(batch_results), batch_errors)

    log.info("Raw generation: %d new queries", len(all_new_queries))

    # Deduplicate against existing
    all_new_queries = deduplicate_queries(all_new_queries, existing_gs)
    log.info("After dedup: %d new queries", len(all_new_queries))

    # Build new golden set = existing + new
    golden_set = list(existing_gs)
    next_id = len(golden_set) + 1

    new_by_cat: dict[str, list[dict]] = defaultdict(list)
    for q in all_new_queries:
        new_by_cat[q["category"]].append(q)

    added_per_cat: Counter[str] = Counter()
    for cat, needed in weak_cats.items():
        pool = new_by_cat.get(cat, [])
        random.shuffle(pool)
        for q in pool[:needed]:
            if q.get("multi_titles"):
                relevant_ids = q["multi_titles"]
            else:
                relevant_ids = [q["title"]]
                if q.get("title2"):
                    relevant_ids.append(q["title2"])

            golden_set.append({
                "id": f"gs6-{next_id:03d}",
                "category": q["category"],
                "question": q["query"],
                "relevant_source_ids": relevant_ids,
                "expected_route": (
                    "sql" if q["category"] in ("named_entity", "fine_lookup") else "rag"
                ),
                "filters": {},
            })
            next_id += 1
            added_per_cat[cat] += 1

    # Summary
    cats = Counter(q["category"] for q in golden_set)
    log.info("=" * 60)
    log.info("Golden Set v6: %d queries total (%d existing + %d new)",
             len(golden_set), len(existing_gs), len(golden_set) - len(existing_gs))
    log.info("=" * 60)
    for cat in sorted(cats.keys()):
        added = added_per_cat.get(cat, 0)
        log.info("  %s: %d total (+%d new)", cat, cats[cat], added)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(golden_set, f, indent=2, ensure_ascii=False)
    log.info("Saved %s", OUTPUT_PATH)

    conn.close()


if __name__ == "__main__":
    main()
