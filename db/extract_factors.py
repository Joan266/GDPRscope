"""
JurisMind — Art. 83(2) factor extraction pipeline

Extracts structured aggravating/mitigating factors from enforcement decisions
using LLM (Nova Micro via OpenRouter). Saves to case_factors table.

Usage:
    PYTHONUTF8=1 python db/extract_factors.py                # all eligible docs
    PYTHONUTF8=1 python db/extract_factors.py --limit 50     # first 50
    PYTHONUTF8=1 python db/extract_factors.py --dry-run      # count only
    PYTHONUTF8=1 python db/extract_factors.py --source tracker  # one source only
"""

import argparse
import json
import logging
import os
import re
import sys
import time

import psycopg
import requests
from psycopg.types.json import Jsonb

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "amazon/nova-micro-v1"
MAX_TOKENS = 800
REQUEST_DELAY = 0.3  # seconds between API calls
BATCH_SIZE = 20      # commit every N docs

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Prompt components ─────────────────────────────────────────────────────────

ART83_2 = """Article 83(2) GDPR - Factors for determining fines:
(a) nature, gravity and duration; number of data subjects affected; level of damage
(b) intentional or negligent character
(c) action taken to mitigate damage to data subjects
(d) degree of responsibility (technical/organisational measures under Art.25/32)
(e) any relevant previous infringements
(f) degree of cooperation with the supervisory authority
(g) categories of personal data affected
(h) manner in which the infringement became known to the authority (self-reported?)
(i) compliance with previously ordered measures
(j) adherence to approved codes of conduct or certification
(k) any other aggravating or mitigating factor (financial benefits gained)"""

SCHEMA = """{
  "factor_a_gravity": {"severity": "low|medium|high", "duration_months": null, "subjects_affected": null, "evidence": "quote or null"},
  "factor_b_intent": {"type": "intentional|negligent|unknown", "evidence": "quote or null"},
  "factor_c_mitigation": {"mitigated": true, "actions": "description or null", "evidence": "quote or null"},
  "factor_d_responsibility": {"had_measures": true, "evidence": "quote or null"},
  "factor_e_prior_violations": {"has_prior": false, "evidence": "quote or null"},
  "factor_f_cooperation": {"cooperated": true, "evidence": "quote or null"},
  "factor_g_data_categories": {"sensitive": false, "categories": ["list"], "evidence": "quote or null"},
  "factor_h_discovery": {"self_reported": true, "evidence": "quote or null"},
  "factor_i_prior_orders": {"complied": null, "evidence": "quote or null"},
  "factor_j_certification": {"has_certification": null, "evidence": "quote or null"},
  "factor_k_other": {"aggravating": [], "mitigating": [], "evidence": "quote or null"},
  "data_subjects_affected": "number or 'unknown'",
  "overall_assessment": "one sentence summary of the fine rationale"
}"""

SYSTEM_PROMPT = """You are a legal data extraction system specializing in GDPR enforcement.
Extract ONLY information explicitly stated in the text. Use null for missing information.
Return ONLY valid JSON — no explanation, no markdown fences, no commentary."""


def build_prompt(doc: dict) -> str:
    """Build extraction prompt for a single document."""
    articles = ", ".join(doc["gdpr_articles"] or [])
    facts = (doc["summary_facts"] or "")[:1500]
    holding = (doc["summary_holding"] or "")[:2000]
    full_text = (doc["full_text"] or "")[:3000] if doc.get("full_text") else ""

    text_section = ""
    if facts:
        text_section += f"\n=== Facts ===\n{facts}"
    if holding:
        text_section += f"\n\n=== Holding ===\n{holding}"
    if full_text and not holding:
        text_section += f"\n\n=== Full Text (excerpt) ===\n{full_text}"

    return f"""Extract structured Art. 83(2) GDPR factors from this enforcement decision.
Return ONLY valid JSON matching this schema:

{SCHEMA}

{ART83_2}

=== Case: {doc['title']} ===
Jurisdiction: {doc['jurisdiction'] or 'Unknown'}
Fine: EUR {doc['fine_amount']:,.0f}
GDPR Articles: {articles}
{text_section}"""


# ── LLM call ──────────────────────────────────────────────────────────────────

def call_llm(prompt: str) -> dict:
    """Call OpenRouter API and return parsed result."""
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OR_KEY}",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    cost = data.get("usage", {}).get("cost", 0) or 0
    tokens = data.get("usage", {}).get("total_tokens", 0)
    content = data["choices"][0]["message"]["content"] or ""

    parsed = _parse_json(content)
    return {"cost": cost, "tokens": tokens, "parsed": parsed, "raw": content}


def _parse_json(content: str) -> dict | None:
    """Try to parse JSON from LLM response, handling markdown fences."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { to last }
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ── DB operations ─────────────────────────────────────────────────────────────

FETCH_SQL = """
    SELECT d.id, d.title, d.jurisdiction, d.fine_amount,
           d.gdpr_articles, d.summary_facts, d.summary_holding, d.full_text
    FROM documents d
    LEFT JOIN case_factors cf ON cf.document_id = d.id
    WHERE d.fine_amount > 0
      AND cf.id IS NULL
      AND (d.summary_holding IS NOT NULL OR d.full_text IS NOT NULL)
"""

UPSERT_SQL = """
    INSERT INTO case_factors (
        document_id, factor_a_gravity, factor_b_intent,
        factor_c_mitigation, factor_d_responsibility,
        factor_e_prior_violations, factor_f_cooperation,
        factor_g_data_categories, factor_h_discovery,
        data_subjects_affected, overall_assessment,
        extraction_model, extraction_cost
    ) VALUES (
        %(document_id)s, %(factor_a_gravity)s, %(factor_b_intent)s,
        %(factor_c_mitigation)s, %(factor_d_responsibility)s,
        %(factor_e_prior_violations)s, %(factor_f_cooperation)s,
        %(factor_g_data_categories)s, %(factor_h_discovery)s,
        %(data_subjects_affected)s, %(overall_assessment)s,
        %(extraction_model)s, %(extraction_cost)s
    )
    ON CONFLICT (document_id) DO UPDATE SET
        factor_a_gravity = EXCLUDED.factor_a_gravity,
        factor_b_intent = EXCLUDED.factor_b_intent,
        factor_c_mitigation = EXCLUDED.factor_c_mitigation,
        factor_d_responsibility = EXCLUDED.factor_d_responsibility,
        factor_e_prior_violations = EXCLUDED.factor_e_prior_violations,
        factor_f_cooperation = EXCLUDED.factor_f_cooperation,
        factor_g_data_categories = EXCLUDED.factor_g_data_categories,
        factor_h_discovery = EXCLUDED.factor_h_discovery,
        data_subjects_affected = EXCLUDED.data_subjects_affected,
        overall_assessment = EXCLUDED.overall_assessment,
        extraction_model = EXCLUDED.extraction_model,
        extraction_cost = EXCLUDED.extraction_cost
"""


def fetch_eligible(conn: psycopg.Connection, source: str | None, limit: int) -> list[dict]:
    """Fetch documents eligible for factor extraction."""
    sql = FETCH_SQL
    params: list = []
    if source:
        sql += " AND d.source = %s"
        params.append(source)
    sql += " ORDER BY d.fine_amount DESC"
    if limit > 0:
        sql += " LIMIT %s"
        params.append(limit)

    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [
        {
            "id": r[0], "title": r[1], "jurisdiction": r[2],
            "fine_amount": r[3], "gdpr_articles": r[4],
            "summary_facts": r[5], "summary_holding": r[6],
            "full_text": r[7],
        }
        for r in rows
    ]


def save_factors(conn: psycopg.Connection, doc_id: str, parsed: dict,
                 cost: float) -> None:
    """Save extracted factors to case_factors table."""

    def _jsonb(key: str) -> Jsonb | None:
        val = parsed.get(key)
        return Jsonb(val) if val is not None else None

    params = {
        "document_id": doc_id,
        "factor_a_gravity": _jsonb("factor_a_gravity"),
        "factor_b_intent": _jsonb("factor_b_intent"),
        "factor_c_mitigation": _jsonb("factor_c_mitigation"),
        "factor_d_responsibility": _jsonb("factor_d_responsibility"),
        "factor_e_prior_violations": _jsonb("factor_e_prior_violations"),
        "factor_f_cooperation": _jsonb("factor_f_cooperation"),
        "factor_g_data_categories": _jsonb("factor_g_data_categories"),
        "factor_h_discovery": _jsonb("factor_h_discovery"),
        "data_subjects_affected": str(parsed.get("data_subjects_affected", "unknown")),
        "overall_assessment": parsed.get("overall_assessment"),
        "extraction_model": MODEL,
        "extraction_cost": cost,
    }
    conn.cursor().execute(UPSERT_SQL, params)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Art. 83(2) factors")
    parser.add_argument("--limit", type=int, default=0, help="Max docs (0=all)")
    parser.add_argument("--source", type=str, default=None,
                        help="Filter by source (tracker/gdprhub)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count eligible docs without extracting")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("DATABASE_URL not set")
    if not OR_KEY and not args.dry_run:
        sys.exit("OPENROUTER_API_KEY not set")

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    docs = fetch_eligible(conn, args.source, args.limit)
    log.info("Eligible documents: %d", len(docs))

    if args.dry_run:
        for d in docs[:10]:
            log.info("  %s | EUR %.0f | %s", d["title"][:60], d["fine_amount"],
                     d["jurisdiction"])
        conn.close()
        return

    if not docs:
        log.info("No documents to process.")
        conn.close()
        return

    total_cost = 0.0
    success = 0
    failed = 0

    for i, doc in enumerate(docs, 1):
        try:
            prompt = build_prompt(doc)
            result = call_llm(prompt)
            total_cost += result["cost"]

            if result["parsed"]:
                save_factors(conn, doc["id"], result["parsed"], result["cost"])
                success += 1
            else:
                log.warning("[%d/%d] %s — JSON parse failed", i, len(docs),
                            doc["title"][:50])
                failed += 1

            if i % 50 == 0:
                log.info("[%d/%d] processed | %d ok, %d failed | $%.4f spent",
                         i, len(docs), success, failed, total_cost)

        except requests.exceptions.RequestException as e:
            log.error("[%d/%d] %s — API error: %s", i, len(docs),
                      doc["title"][:50], e)
            failed += 1
            time.sleep(5)  # back off on API errors
        except Exception as e:
            log.error("[%d/%d] %s — unexpected error: %s", i, len(docs),
                      doc["title"][:50], e)
            failed += 1

        time.sleep(REQUEST_DELAY)

    conn.close()
    log.info("=" * 60)
    log.info("Extraction complete: %d success, %d failed, $%.4f total cost",
             success, failed, total_cost)


if __name__ == "__main__":
    main()
