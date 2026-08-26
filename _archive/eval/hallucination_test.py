"""
Hallucination Comparative Test — Claude (no context) vs GDPRScope (RAG)

Tests the same queries against:
  1. Claude direct (no retrieval, pure parametric knowledge)
  2. GDPRScope RAG (retrieval + grounded generation)

Then verifies every factual claim against the real database.

Claim types:
  - supported:    claim matches data in DB (verified)
  - unsupported:  claim not found in DB (could be hallucination or missing data)
  - fabricated:   claim references a case/entity that doesn't exist in DB at all
  - distorted:    claim references a real case but attributes wrong fine/article/date
  - embellished:  claim adds plausible detail that can't be verified either way

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python eval/hallucination_test.py
    PYTHONUTF8=1 python eval/hallucination_test.py --out eval/hallucination_results.json
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import anthropic
import psycopg

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.rag import (
    MODEL_ID_LLM,
    MODEL_ID_HAIKU,
    build_prompt,
    call_llm,
    embed_query,
    fetch_parent_context,
    make_bedrock_client,
    reciprocal_rank_fusion,
    search_text_chunks,
    search_vector_chunks,
    extract_intent,
    apply_intent_filters,
    _find_controller_docs,
    expand_query,
    search_question_chunks,
    search_headnote_chunks,
    _fetch_fine_sorted_chunks,
    _fetch_chunks_for_case_numbers,
    hyde_embed,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Test queries ──────────────────────────────────────────────────────────────
# Designed to expose hallucination patterns: specific case numbers, exact fines,
# article citations, and cross-jurisdictional comparisons.

TEST_QUERIES = [
    {
        "id": "ht-001",
        "question": (
            "What fine did AEPD impose on Vodafone España in case PS/00059/2020 "
            "and what GDPR articles were violated?"
        ),
        "category": "specific_case",
        "verify_fields": ["case_number", "fine_amount", "articles", "controller"],
    },
    {
        "id": "ht-002",
        "question": (
            "What was the outcome of the CNIL decision against Google LLC "
            "regarding cookie consent? What was the fine amount?"
        ),
        "category": "specific_case",
        "verify_fields": ["fine_amount", "articles", "authority"],
    },
    {
        "id": "ht-003",
        "question": (
            "What GDPR precedents exist for fines related to Article 32 "
            "security measures in the healthcare sector?"
        ),
        "category": "conceptual",
        "verify_fields": ["case_existence", "articles", "sector"],
    },
    {
        "id": "ht-004",
        "question": (
            "Has any European DPA fined a company for using employee "
            "biometric data (fingerprints) for time tracking? "
            "Cite specific cases and fines."
        ),
        "category": "sector_specific",
        "verify_fields": ["case_existence", "fine_amount", "articles"],
    },
    {
        "id": "ht-005",
        "question": (
            "Compare the enforcement approaches of AEPD (Spain) and CNIL (France) "
            "regarding data breach notification delays under Article 33. "
            "Cite specific cases with fines."
        ),
        "category": "cross_jurisdiction",
        "verify_fields": ["case_existence", "fine_amount", "jurisdiction"],
    },
]


# ── Claude direct (no context) ───────────────────────────────────────────────

def query_claude_direct(client: anthropic.Anthropic, question: str) -> str:
    """Ask Claude the question without any retrieval context."""
    msg = client.messages.create(
        model=MODEL_ID_LLM,
        max_tokens=1500,
        temperature=0.0,
        system=(
            "You are a GDPR enforcement expert. Answer the question with specific "
            "case references, fine amounts, and GDPR articles where possible. "
            "If you cite a case, include the case number and DPA."
        ),
        messages=[{"role": "user", "content": question}],
    )
    return msg.content[0].text.strip()


# ── GDPRScope RAG ────────────────────────────────────────────────────────────

def query_gdprscope_rag(
    conn: psycopg.Connection, question: str
) -> tuple[str, list[dict]]:
    """Full RAG pipeline: retrieve + generate. Returns (response, contexts)."""
    bedrock_client = make_bedrock_client()
    filters: dict = {}

    intent = extract_intent(question)
    if intent:
        apply_intent_filters(intent, filters)
        if intent.controller_name:
            doc_ids = _find_controller_docs(conn.cursor(), intent.controller_name)
            if doc_ids:
                filters["doc_ids"] = doc_ids

    # Embedding
    if intent and intent.gdpr_articles:
        try:
            query_vec = hyde_embed(question, intent)
        except Exception:
            query_vec = embed_query(bedrock_client, question)
    else:
        query_vec = embed_query(bedrock_client, question)

    cur = conn.cursor()
    vector_hits = search_vector_chunks(cur, query_vec, 20, filters)
    text_hits = search_text_chunks(cur, question, 20, filters)
    question_hits = search_question_chunks(cur, query_vec, 20, filters)
    headnote_hits = search_headnote_chunks(cur, query_vec, 20, filters)

    fine_hits: list[str] = []
    if intent and intent.sort_by == "fine_desc":
        fine_hits = _fetch_fine_sorted_chunks(cur, 20, filters)

    case_hits = _fetch_chunks_for_case_numbers(cur, question)

    # Query expansion
    expansion_arms: list[list[str]] = []
    is_conceptual = not (intent and (intent.controller_name or intent.sort_by))
    if is_conceptual:
        variants = expand_query(question)
        for variant in variants:
            v_vec = embed_query(bedrock_client, variant)
            v_hits = search_vector_chunks(cur, v_vec, 20, filters)
            if v_hits:
                expansion_arms.append(v_hits)

    rrf_ranked = reciprocal_rank_fusion(
        vector_hits, text_hits, fine_hits or None,
        question_hits or None, headnote_hits or None,
        *(arm for arm in expansion_arms),
    )
    rrf_scores = dict(rrf_ranked)
    if case_hits:
        for cid in case_hits:
            rrf_scores[cid] = max(rrf_scores.get(cid, 0.0), 1.0)
    top_child_ids = case_hits + [cid for cid, _ in rrf_ranked[:30]
                                 if cid not in set(case_hits)]

    contexts = fetch_parent_context(cur, top_child_ids, rrf_scores, 8)

    system_p, user_p = build_prompt(question, contexts, [])
    response = call_llm(bedrock_client, system_p, user_p)

    return response, contexts


# ── Claim extraction & DB verification ────────────────────────────────────────

_CLAIM_EXTRACT_PROMPT = """\
You are a legal fact-checker. Extract every verifiable factual claim from this response.

Focus on claims that can be checked against a database of GDPR enforcement decisions:
- Case numbers (e.g., PS/00059/2020, SAN-2020-015)
- Fine amounts (e.g., EUR 8,125,000)
- GDPR articles cited (e.g., Article 32, Art. 6(1)(a))
- Controller/company names
- DPA/authority names
- Decision dates or years
- Specific factual findings (e.g., "data breach affected 50,000 users")

For each claim, extract:
- "text": the claim as stated
- "type": one of "case_reference", "fine_amount", "article_citation", "factual_detail"
- "entity": the company or case number referenced (if any)
- "value": the specific value claimed (fine amount, article number, etc.)

Response:
{response}

Return ONLY a JSON array of claim objects. No commentary."""


_CLAIM_VERIFY_PROMPT = """\
You are verifying factual claims against real database records.

## Claim to verify:
{claim_text}

## Database records found for this entity/case:
{db_records}

## Classify this claim as one of:
- "verified": The claim matches the database records exactly
- "distorted": The claim references a real case/entity but gets a detail wrong \
(wrong fine amount, wrong article, wrong date)
- "fabricated": The claim references a case number or entity that does NOT appear \
in the database at all
- "unverifiable": The claim is plausible but the database doesn't have enough \
info to confirm or deny (e.g., procedural details, quotes from decisions)

Return ONLY a JSON object: {{"label": "...", "reason": "one sentence"}}"""


def extract_claims(client: anthropic.Anthropic, response: str) -> list[dict]:
    """Extract verifiable claims from a response using LLM."""
    msg = client.messages.create(
        model=MODEL_ID_HAIKU,
        max_tokens=2000,
        temperature=0.0,
        messages=[{"role": "user", "content":
                   _CLAIM_EXTRACT_PROMPT.format(response=response[:3000])}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except Exception:
        log.warning("Failed to parse claims: %s", raw[:200])
        return []


def verify_claim_against_db(
    cur: psycopg.Cursor,
    client: anthropic.Anthropic,
    claim: dict,
) -> dict:
    """Verify a single claim against the actual database."""
    entity = claim.get("entity", "")
    claim_text = claim.get("text", "")
    value = claim.get("value", "")

    # Try to find matching records in DB
    db_records = []

    # Search by case number
    case_patterns = re.findall(
        r'(?:PS|PD|EXP|SAN|IN)[-/\s]*\d{4,}[-/\s]*\d{0,4}',
        claim_text, re.IGNORECASE
    )
    if case_patterns:
        for pat in case_patterns[:3]:
            clean = re.sub(r'\s+', '', pat)
            cur.execute(
                "SELECT title, fine_amount, gdpr_articles, jurisdiction, "
                "decision_date, controller_name FROM documents "
                "WHERE title ILIKE %s OR source_id ILIKE %s LIMIT 3",
                (f"%{clean}%", f"%{clean}%"),
            )
            for row in cur.fetchall():
                db_records.append({
                    "title": row[0], "fine_amount": row[1],
                    "articles": row[2], "jurisdiction": row[3],
                    "date": str(row[4]) if row[4] else None,
                    "controller": row[5],
                })

    # Search by entity name
    if entity and not db_records:
        cur.execute(
            "SELECT title, fine_amount, gdpr_articles, jurisdiction, "
            "decision_date, controller_name FROM documents "
            "WHERE controller_name ILIKE %s OR title ILIKE %s "
            "ORDER BY fine_amount DESC NULLS LAST LIMIT 5",
            (f"%{entity}%", f"%{entity}%"),
        )
        for row in cur.fetchall():
            db_records.append({
                "title": row[0], "fine_amount": row[1],
                "articles": row[2], "jurisdiction": row[3],
                "date": str(row[4]) if row[4] else None,
                "controller": row[5],
            })

    # If no DB records found and claim references specific case → likely fabricated
    if not db_records and case_patterns:
        return {**claim, "label": "fabricated",
                "reason": f"Case {case_patterns[0]} not found in DB"}

    if not db_records and not entity:
        return {**claim, "label": "unverifiable",
                "reason": "No entity/case reference to check against"}

    if not db_records:
        return {**claim, "label": "unverifiable",
                "reason": f"Entity '{entity}' not found in DB"}

    # Use LLM to compare claim against DB records
    db_text = json.dumps(db_records[:3], indent=2, default=str, ensure_ascii=False)
    msg = client.messages.create(
        model=MODEL_ID_HAIKU,
        max_tokens=200,
        temperature=0.0,
        messages=[{"role": "user", "content":
                   _CLAIM_VERIFY_PROMPT.format(
                       claim_text=claim_text,
                       db_records=db_text[:2000],
                   )}],
    )
    raw = msg.content[0].text.strip()
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        return {**claim, "label": result.get("label", "unverifiable"),
                "reason": result.get("reason", "")}
    except Exception:
        return {**claim, "label": "unverifiable", "reason": "Parse error"}


# ── Main ──────────────────────────────────────────────────────────────────────

def run_test(conn: psycopg.Connection, out_path: str | None = None) -> None:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    cur = conn.cursor()
    results = []

    for i, q in enumerate(TEST_QUERIES, 1):
        qid = q["id"]
        question = q["question"]
        log.info("[%d/%d] %s — %s", i, len(TEST_QUERIES), qid, question[:70])

        # ── Claude direct ──
        log.info("  Claude direct (no context)...")
        t0 = time.monotonic()
        claude_response = query_claude_direct(client, question)
        claude_ms = int((time.monotonic() - t0) * 1000)

        log.info("  Extracting claims from Claude response...")
        claude_claims = extract_claims(client, claude_response)
        log.info("  Verifying %d claims against DB...", len(claude_claims))
        for j, claim in enumerate(claude_claims):
            claude_claims[j] = verify_claim_against_db(cur, client, claim)

        # ── GDPRScope RAG ──
        log.info("  GDPRScope RAG...")
        t0 = time.monotonic()
        rag_response, rag_contexts = query_gdprscope_rag(conn, question)
        rag_ms = int((time.monotonic() - t0) * 1000)

        log.info("  Extracting claims from RAG response...")
        rag_claims = extract_claims(client, rag_response)
        log.info("  Verifying %d claims against DB...", len(rag_claims))
        for j, claim in enumerate(rag_claims):
            rag_claims[j] = verify_claim_against_db(cur, client, claim)

        # ── Summary ──
        def _stats(claims: list[dict]) -> dict:
            total = len(claims)
            if total == 0:
                return {"total": 0, "verified": 0, "distorted": 0,
                        "fabricated": 0, "unverifiable": 0}
            return {
                "total": total,
                "verified": sum(1 for c in claims if c.get("label") == "verified"),
                "distorted": sum(1 for c in claims if c.get("label") == "distorted"),
                "fabricated": sum(1 for c in claims if c.get("label") == "fabricated"),
                "unverifiable": sum(1 for c in claims if c.get("label") == "unverifiable"),
            }

        claude_stats = _stats(claude_claims)
        rag_stats = _stats(rag_claims)

        log.info("  Claude: %d claims — %d verified, %d distorted, %d fabricated",
                 claude_stats["total"], claude_stats["verified"],
                 claude_stats["distorted"], claude_stats["fabricated"])
        log.info("  RAG:    %d claims — %d verified, %d distorted, %d fabricated",
                 rag_stats["total"], rag_stats["verified"],
                 rag_stats["distorted"], rag_stats["fabricated"])

        results.append({
            "id": qid,
            "question": question,
            "category": q["category"],
            "claude": {
                "response": claude_response[:4000],
                "latency_ms": claude_ms,
                "claims": claude_claims,
                "stats": claude_stats,
            },
            "rag": {
                "response": rag_response[:4000],
                "latency_ms": rag_ms,
                "claims": rag_claims,
                "stats": rag_stats,
                "context_titles": [c.get("title", "") for c in rag_contexts[:5]],
            },
        })

    # ── Print report ──
    _print_report(results)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        log.info("Results saved to %s", out_path)


def _print_report(results: list[dict]) -> None:
    print(f"\n{'=' * 72}")
    print("HALLUCINATION COMPARATIVE TEST — Claude vs GDPRScope")
    print(f"{'=' * 72}\n")

    totals = {"claude": {"verified": 0, "distorted": 0, "fabricated": 0,
                         "unverifiable": 0, "total": 0},
              "rag": {"verified": 0, "distorted": 0, "fabricated": 0,
                      "unverifiable": 0, "total": 0}}

    for r in results:
        print(f"── {r['id']} ({r['category']}) ──")
        print(f"   Q: {r['question'][:70]}...")
        for system in ["claude", "rag"]:
            s = r[system]["stats"]
            total = s["total"] or 1
            label = "Claude (no context)" if system == "claude" else "GDPRScope (RAG)"
            print(f"   {label:22s}: {s['total']:2d} claims | "
                  f"✓ {s['verified']:2d} verified | "
                  f"⚠ {s['distorted']:2d} distorted | "
                  f"✗ {s['fabricated']:2d} fabricated | "
                  f"? {s['unverifiable']:2d} unverifiable")
            for k in totals[system]:
                totals[system][k] += s[k]

        # Show fabricated claims from Claude
        fabricated = [c for c in r["claude"]["claims"]
                      if c.get("label") == "fabricated"]
        if fabricated:
            print(f"   Claude fabrications:")
            for c in fabricated[:3]:
                print(f"     ✗ {c['text'][:80]}")
                print(f"       → {c.get('reason', '')}")
        print()

    # ── Totals ──
    print(f"{'=' * 72}")
    print("TOTALS")
    print(f"{'=' * 72}")
    for system, label in [("claude", "Claude (no context)"),
                          ("rag", "GDPRScope (RAG)")]:
        t = totals[system]
        total = t["total"] or 1
        halluc = t["fabricated"] + t["distorted"]
        print(f"\n  {label}:")
        print(f"    Total claims      : {t['total']}")
        print(f"    Verified          : {t['verified']:3d}  ({t['verified']/total*100:5.1f}%)")
        print(f"    Distorted         : {t['distorted']:3d}  ({t['distorted']/total*100:5.1f}%)")
        print(f"    Fabricated        : {t['fabricated']:3d}  ({t['fabricated']/total*100:5.1f}%)")
        print(f"    Unverifiable      : {t['unverifiable']:3d}  ({t['unverifiable']/total*100:5.1f}%)")
        print(f"    ─────────────────────")
        print(f"    Hallucination rate: {halluc/total*100:.1f}% "
              f"(fabricated + distorted)")
        print(f"    Grounding rate    : {t['verified']/total*100:.1f}% "
              f"(verified / total)")

    print(f"\n{'=' * 72}")
    print("INTERPRETATION")
    print(f"{'=' * 72}")
    print("""
  - 'fabricated': The response cites a case/entity that doesn't exist in our
    verified DB of 6,751 GDPR decisions. This is the most dangerous type —
    a lawyer submitting a fabricated citation can be sanctioned.

  - 'distorted': The case exists but the response gets a detail wrong (wrong
    fine amount, wrong article, wrong year). Still problematic but less severe.

  - 'unverifiable': The claim can't be checked either way — usually procedural
    details or quotes that aren't in our DB fields. Not necessarily wrong.

  - 'verified': The claim matches our database exactly. This is what we want.
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hallucination comparative test")
    parser.add_argument("--out", default=None, help="Save results to JSON")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("DATABASE_URL not set")
    if not ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY not set")

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    run_test(conn, args.out)
    conn.close()
