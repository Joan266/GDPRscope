"""Build golden_set_v4.json — doc-first methodology.

Generates queries FROM documents (not the reverse) so each query is
guaranteed to have at least one relevant document.

Steps:
1. Sample ~350 diverse docs from DB (stratified by jurisdiction, source, fine)
2. For each doc, LLM generates 1 query + assigns a category
3. Filter trivials/duplicates → target 250 queries
4. Output golden_set_v4.json

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python eval/build_golden_v4.py
"""

import json
import logging
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import openai
import psycopg

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATABASE_URL = os.environ["DATABASE_URL"]
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL_ID = "moonshotai/kimi-k2"
OUTPUT_PATH = Path("eval/golden_set_v4.json")

CATEGORIES = [
    "named_entity",      # about a specific controller/company
    "article_lookup",    # about a specific GDPR article's enforcement
    "conceptual",        # abstract legal concept or principle
    "scenario",          # hypothetical fact pattern
    "fine_lookup",       # about fine amounts / rankings
    "cross_jurisdiction",# comparing DPA approaches across countries
    "sector",            # industry-specific enforcement patterns
    "edge_case",         # unusual or novel GDPR applications
    "false_premise",     # query with intentional factual error
]

TARGET_PER_CATEGORY = 28  # 28 × 9 = 252

_QUERY_GEN_PROMPT = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE search query that a privacy lawyer or DPO would naturally ask, where this document would be the best answer.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Controller: {controller}
- Facts: {facts}
- Holding: {holding}

RULES:
1. The query must be answerable by this specific document
2. Write as a real user would search — natural language, not keywords
3. Vary the query style: some should ask about the company, some about the legal principle, some about the fine, some pose hypothetical scenarios
4. Assign ONE category from: {categories}
5. For "false_premise" category: intentionally include a wrong fact (wrong DPA, wrong fine amount, wrong article) that this document would correct

Return ONLY valid JSON:
{{"query": "...", "category": "..."}}"""


def get_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )


def sample_docs(conn: psycopg.Connection) -> list[dict]:
    """Sample diverse documents — stratified by jurisdiction, source, fine presence."""
    cur = conn.cursor()

    # Only docs with embedded chunks (searchable in RAG)
    cur.execute("""
        SELECT d.source_id, d.title, d.authority, d.jurisdiction,
               d.fine_amount, d.fine_currency, d.gdpr_articles,
               d.violation_type, d.outcome, d.controller_name,
               d.case_number, d.source, d.content_depth,
               left(d.summary_facts, 500) as facts,
               left(d.summary_holding, 500) as holding
        FROM documents d
        WHERE EXISTS (
            SELECT 1 FROM chunks c
            WHERE c.document_id = d.id
              AND c.chunk_type = 'child'
              AND c.embedding IS NOT NULL
        )
        AND d.source IN ('gdprhub', 'enforcement_tracker')
    """)

    cols = [desc[0] for desc in cur.description]
    all_docs = [dict(zip(cols, row)) for row in cur.fetchall()]
    log.info("Total searchable docs: %d", len(all_docs))

    # Stratified sampling
    selected: list[dict] = []

    # 1. GDPRhub docs with full text (best for query generation)
    full_docs = [d for d in all_docs if d["source"] == "gdprhub"
                 and d["facts"] and len(d["facts"]) > 100]
    log.info("GDPRhub with facts: %d", len(full_docs))

    # 2. Group by jurisdiction for diversity
    by_juris: dict[str, list[dict]] = defaultdict(list)
    for d in full_docs:
        by_juris[d["jurisdiction"]].append(d)

    # Sample proportionally from each jurisdiction, min 2 per jurisdiction
    target_full = 250
    juris_counts = {j: len(docs) for j, docs in by_juris.items()}
    total_available = sum(juris_counts.values())

    for juris, docs in sorted(by_juris.items(), key=lambda x: -len(x[1])):
        # Proportional allocation with floor of 2
        n = max(2, int(target_full * len(docs) / total_available))
        n = min(n, len(docs))

        # Prefer docs with fines (more interesting queries)
        with_fine = [d for d in docs if d.get("fine_amount") and d["fine_amount"] > 0]
        without_fine = [d for d in docs if not d.get("fine_amount") or d["fine_amount"] == 0]

        random.shuffle(with_fine)
        random.shuffle(without_fine)

        # Take 60% with fine, 40% without (if available)
        n_fine = min(len(with_fine), int(n * 0.6) + 1)
        n_no_fine = min(len(without_fine), n - n_fine)
        sample = with_fine[:n_fine] + without_fine[:n_no_fine]
        selected.extend(sample)

    # 3. Add enforcement_tracker docs with high fines (for fine_lookup queries)
    tracker_docs = [d for d in all_docs if d["source"] == "enforcement_tracker"
                    and d.get("fine_amount") and d["fine_amount"] > 100_000]
    random.shuffle(tracker_docs)
    selected.extend(tracker_docs[:50])

    random.shuffle(selected)
    log.info("Sampled %d docs (%d jurisdictions)",
             len(selected), len(set(d["jurisdiction"] for d in selected)))
    return selected


def generate_query(client: openai.OpenAI, doc: dict, target_category: str | None = None) -> dict | None:
    """Generate one query for a document via LLM."""
    fine_str = (f"{doc.get('fine_currency', 'EUR')} {doc['fine_amount']:,.0f}"
                if doc.get("fine_amount") else "No fine")
    articles = ", ".join(doc.get("gdpr_articles") or []) or "Not specified"

    categories_str = ", ".join(CATEGORIES)
    if target_category:
        categories_str = target_category

    prompt = _QUERY_GEN_PROMPT.format(
        title=doc.get("title", "")[:120],
        authority=doc.get("authority", "Unknown"),
        jurisdiction=doc.get("jurisdiction", "Unknown"),
        fine_str=fine_str,
        articles=articles,
        outcome=doc.get("outcome", "Unknown"),
        controller=doc.get("controller_name") or "Not specified",
        facts=(doc.get("facts") or "Not available")[:400],
        holding=(doc.get("holding") or "Not available")[:400],
        categories=categories_str,
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            max_tokens=200,
            temperature=0.7,  # some variety
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        query_text = data.get("query", "").strip()
        category = data.get("category", "conceptual").strip()

        if not query_text or len(query_text) < 15:
            return None
        if category not in CATEGORIES:
            category = "conceptual"

        return {
            "query": query_text,
            "category": category,
            "source_id": doc["source_id"],
        }
    except Exception as exc:
        log.debug("Query generation failed for %s: %s", doc.get("title", "")[:40], exc)
        return None


def deduplicate(queries: list[dict]) -> list[dict]:
    """Remove near-duplicate queries (same source_id or very similar text)."""
    seen_sources: set[str] = set()
    seen_prefixes: set[str] = set()
    result: list[dict] = []

    for q in queries:
        sid = q["source_id"]
        # Allow max 2 queries per source doc
        source_count = sum(1 for r in result if r["source_id"] == sid)
        if source_count >= 2:
            continue

        # Check text similarity (prefix-based dedup)
        prefix = re.sub(r'\s+', ' ', q["query"].lower()[:60])
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)
        result.append(q)

    return result


def balance_categories(queries: list[dict]) -> list[dict]:
    """Balance queries across categories, targeting TARGET_PER_CATEGORY each."""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for q in queries:
        by_cat[q["category"]].append(q)

    log.info("Category distribution before balancing:")
    for cat in CATEGORIES:
        log.info("  %s: %d", cat, len(by_cat.get(cat, [])))

    balanced: list[dict] = []
    for cat in CATEGORIES:
        pool = by_cat.get(cat, [])
        random.shuffle(pool)
        balanced.extend(pool[:TARGET_PER_CATEGORY])

    return balanced


def main() -> None:
    if not OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY not set")
        sys.exit(1)

    random.seed(42)
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    client = get_client()

    # 1. Sample docs
    docs = sample_docs(conn)

    # 2. Generate queries — first pass: let LLM choose category freely
    all_queries: list[dict] = []
    errors = 0

    log.info("Generating queries for %d docs...", len(docs))
    for i, doc in enumerate(docs):
        result = generate_query(client, doc)
        if result:
            all_queries.append(result)
        else:
            errors += 1

        if (i + 1) % 25 == 0:
            cats = Counter(q["category"] for q in all_queries)
            log.info("  %d/%d done (%d errors) — cats: %s",
                     i + 1, len(docs), errors, dict(cats))
            time.sleep(0.1)  # rate limit courtesy

    log.info("First pass: %d queries generated (%d errors)", len(all_queries), errors)

    # 3. Check which categories are underrepresented
    cat_counts = Counter(q["category"] for q in all_queries)
    underrepresented = [cat for cat in CATEGORIES if cat_counts.get(cat, 0) < TARGET_PER_CATEGORY]

    if underrepresented:
        log.info("Underrepresented categories: %s", underrepresented)
        # Second pass: targeted generation for weak categories
        remaining_docs = [d for d in docs if d["source_id"] not in
                         {q["source_id"] for q in all_queries}]
        random.shuffle(remaining_docs)

        for cat in underrepresented:
            needed = TARGET_PER_CATEGORY - cat_counts.get(cat, 0)
            log.info("  Generating %d more for '%s'...", needed, cat)
            generated = 0
            for doc in remaining_docs[:needed * 3]:
                result = generate_query(client, doc, target_category=cat)
                if result and result["category"] == cat:
                    all_queries.append(result)
                    generated += 1
                    if generated >= needed:
                        break
            log.info("  → generated %d for '%s'", generated, cat)

    # 4. Deduplicate
    all_queries = deduplicate(all_queries)
    log.info("After dedup: %d queries", len(all_queries))

    # 5. Balance
    balanced = balance_categories(all_queries)

    # 6. Format output
    golden_set: list[dict] = []
    for i, q in enumerate(balanced, start=1):
        golden_set.append({
            "id": f"gs4-{i:03d}",
            "category": q["category"],
            "question": q["query"],
            "relevant_source_ids": [q["source_id"]],
            "expected_route": "sql" if q["category"] in ("named_entity", "fine_lookup") else "rag",
            "filters": {},
        })

    # 7. Summary
    cats = Counter(q["category"] for q in golden_set)
    total_ids = sum(len(q["relevant_source_ids"]) for q in golden_set)
    log.info("=" * 55)
    log.info("Golden Set v4: %d queries, %d relevant docs", len(golden_set), total_ids)
    log.info("=" * 55)
    for c, n in sorted(cats.items()):
        log.info("  %s: %d queries", c, n)

    # 8. Save
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(golden_set, f, indent=2, ensure_ascii=False)
    log.info("Saved %s", OUTPUT_PATH)

    conn.close()


if __name__ == "__main__":
    main()
