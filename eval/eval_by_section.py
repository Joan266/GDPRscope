"""Eval retrieval by section — measures how each section performs independently.

Runs vector search filtered by each section and computes HR@5 and MRR
per query category. Shows which sections are useful for which query types.

Usage:
    PYTHONUTF8=1 python eval/eval_by_section.py
"""

import json
import logging
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.rag import embed_query, vector_to_pg, extract_intent, EMBED_DIMS

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GOLDEN_PATH = Path(__file__).parent / "golden_set_v3.json"
K = 5

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")

SECTIONS = ["teaser", "facts", "holding", "headnote", "dispute"]

# Also test "all" (no section filter) as baseline
ALL_MODES = SECTIONS + ["all_no_filter"]

_SQL_SECTION = """
SELECT c.id, c.document_id
FROM   chunks c
JOIN   documents d ON d.id = c.document_id
WHERE  c.chunk_type = 'child'
  AND  c.embedding  IS NOT NULL
  AND  c.section    = %s
ORDER BY c.embedding <=> %s::VECTOR({dims})
LIMIT  %s
""".format(dims=EMBED_DIMS)

_SQL_ALL = """
SELECT c.id, c.document_id
FROM   chunks c
JOIN   documents d ON d.id = c.document_id
WHERE  c.chunk_type = 'child'
  AND  c.embedding  IS NOT NULL
ORDER BY c.embedding <=> %s::VECTOR({dims})
LIMIT  %s
""".format(dims=EMBED_DIMS)


def search_by_section(cur, query_vec: list[float], section: str, k: int) -> list[str]:
    """Search chunks filtered by section, return document_ids."""
    vec_pg = vector_to_pg(query_vec)
    if section == "all_no_filter":
        cur.execute(_SQL_ALL, (vec_pg, k))
    else:
        cur.execute(_SQL_SECTION, (section, vec_pg, k))
    return [str(row[1]) for row in cur.fetchall()]


def compute_metrics(retrieved_doc_ids: list[str], relevant_doc_ids: list[str]) -> dict:
    """Compute HR@K and MRR for a single query."""
    relevant_set = set(relevant_doc_ids)
    hit = any(did in relevant_set for did in retrieved_doc_ids[:K])
    mrr = 0.0
    for i, did in enumerate(retrieved_doc_ids[:K]):
        if did in relevant_set:
            mrr = 1.0 / (i + 1)
            break
    return {"hit": hit, "mrr": mrr}


def main():
    if not DATABASE_URL:
        sys.exit("DATABASE_URL not set")

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)

    queries = golden if isinstance(golden, list) else golden.get("queries", golden.get("questions", []))
    log.info("Golden set: %d queries", len(queries))

    conn = psycopg.connect(DATABASE_URL)

    # Check embedding counts per section
    with conn.cursor() as cur:
        for sec in SECTIONS:
            cur.execute(
                "SELECT count(*) FROM chunks WHERE section = %s AND embedding IS NOT NULL",
                (sec,),
            )
            cnt = cur.fetchone()[0]
            log.info("  %s: %d embedded chunks", sec, cnt)

    # Resolve source_ids to document UUIDs
    all_source_ids = set()
    for q in queries:
        for sid in (q.get("relevant_doc_ids") or q.get("relevant_source_ids", [])):
            all_source_ids.add(sid)

    source_to_uuid: dict[str, str] = {}
    if all_source_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_id, id FROM documents WHERE source_id = ANY(%s)",
                (list(all_source_ids),),
            )
            for source_id, doc_id in cur.fetchall():
                source_to_uuid[source_id] = str(doc_id)
        log.info("Resolved %d/%d source_ids to UUIDs", len(source_to_uuid), len(all_source_ids))

    # Results: section -> category -> list of metrics
    results: dict[str, dict[str, list]] = {s: {} for s in ALL_MODES}

    with conn.cursor() as cur:
        for i, q in enumerate(queries):
            qid = q.get("id", f"q{i}")
            query_text = q.get("query") or q.get("question", "")
            category = q.get("category", "unknown")
            raw_relevant = q.get("relevant_doc_ids") or q.get("relevant_source_ids", [])
            relevant = [source_to_uuid.get(sid, sid) for sid in raw_relevant]

            if not relevant:
                continue

            # Embed query once
            query_vec = embed_query(None, query_text)

            for section in ALL_MODES:
                retrieved = search_by_section(cur, query_vec, section, K)
                metrics = compute_metrics(retrieved, relevant)

                if category not in results[section]:
                    results[section][category] = []
                results[section][category].append(metrics)

            if (i + 1) % 10 == 0:
                log.info("  Processed %d/%d queries", i + 1, len(queries))

    conn.close()

    # Print results table
    categories = sorted(set(
        cat for section_data in results.values() for cat in section_data
    ))

    # Header
    print()
    print("=" * 100)
    print("RETRIEVAL BY SECTION — HR@5 / MRR")
    print("=" * 100)
    header = f"{'Category':<22}"
    for sec in ALL_MODES:
        label = sec[:8]
        header += f" | {label:>14}"
    print(header)
    print("-" * len(header))

    # Per category
    for cat in categories:
        row = f"{cat:<22}"
        for sec in ALL_MODES:
            entries = results[sec].get(cat, [])
            if entries:
                hr = sum(1 for e in entries if e["hit"]) / len(entries)
                mrr = sum(e["mrr"] for e in entries) / len(entries)
                row += f" | {hr:.0%}/{mrr:.3f}   "
            else:
                row += f" | {'—':>14}"
        print(row)

    # Totals
    print("-" * len(header))
    row = f"{'TOTAL':<22}"
    for sec in ALL_MODES:
        all_entries = [e for cat_entries in results[sec].values() for e in cat_entries]
        if all_entries:
            hr = sum(1 for e in all_entries if e["hit"]) / len(all_entries)
            mrr = sum(e["mrr"] for e in all_entries) / len(all_entries)
            row += f" | {hr:.0%}/{mrr:.3f}   "
        else:
            row += f" | {'—':>14}"
    print(row)
    print()

    # Best section per category
    print("MEJOR SECCION POR CATEGORIA:")
    print("-" * 50)
    for cat in categories:
        best_sec = None
        best_mrr = -1
        for sec in ALL_MODES:
            entries = results[sec].get(cat, [])
            if entries:
                mrr = sum(e["mrr"] for e in entries) / len(entries)
                if mrr > best_mrr:
                    best_mrr = mrr
                    best_sec = sec
        if best_sec:
            print(f"  {cat:<22} -> {best_sec:<15} (MRR={best_mrr:.3f})")


if __name__ == "__main__":
    main()
