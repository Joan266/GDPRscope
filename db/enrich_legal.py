"""
JurisMind — Legal metadata enrichment

Two modes:
  1. Deterministic (--mode classify): Maps gdpr_articles → violation_type + legal_basis_at_issue
     Zero hallucination risk. Runs on ALL documents.

  2. LLM headnotes (--mode headnotes): Generates 1-3 legal principle summaries per case
     Uses Haiku. Creates headnote chunks for retrieval.
     Run with --limit N to test before full batch.

Usage:
    PYTHONUTF8=1 python db/enrich_legal.py --mode classify
    PYTHONUTF8=1 python db/enrich_legal.py --mode headnotes --limit 20
    PYTHONUTF8=1 python db/enrich_legal.py --mode headnotes --limit 0  # all
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_SIZE = 50

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Deterministic classification from GDPR articles ──────────────────────────

# Regex prefix matching both "Article 5" and "Art. 5" formats
_A = r"(?:Article|Art\.?)\s+"

# Maps article number patterns to violation types
_ARTICLE_TO_VIOLATION: dict[str, list[str]] = {
    # Art. 5, 6 → legal basis issues
    _A + r"5\b":  ["insufficient_legal_basis"],
    _A + r"6\b":  ["insufficient_legal_basis"],
    # Art. 7 → consent conditions
    _A + r"7\b":  ["insufficient_legal_basis"],
    # Art. 9 → special categories
    _A + r"9\b":  ["insufficient_legal_basis"],
    # Art. 12-14 → transparency
    _A + r"12\b": ["insufficient_transparency"],
    _A + r"13\b": ["insufficient_transparency"],
    _A + r"14\b": ["insufficient_transparency"],
    # Art. 15-22 → data subject rights
    _A + r"15\b": ["non_compliance_data_subject_rights"],
    _A + r"16\b": ["non_compliance_data_subject_rights"],
    _A + r"17\b": ["non_compliance_data_subject_rights"],
    _A + r"18\b": ["non_compliance_data_subject_rights"],
    _A + r"19\b": ["non_compliance_data_subject_rights"],
    _A + r"20\b": ["non_compliance_data_subject_rights"],
    _A + r"21\b": ["non_compliance_data_subject_rights"],
    _A + r"22\b": ["non_compliance_automated_decision"],
    # Art. 25 → privacy by design
    _A + r"25\b": ["insufficient_technical_measures"],
    # Art. 28 → processor agreements
    _A + r"28\b": ["insufficient_data_processing_agreement"],
    # Art. 30 → records of processing
    _A + r"30\b": ["insufficient_data_processing_agreement"],
    # Art. 31 → cooperation with DPA
    _A + r"31\b": ["insufficient_cooperation_dpa"],
    # Art. 32 → security of processing
    _A + r"32\b": ["insufficient_technical_measures"],
    # Art. 33, 34 → breach notification
    _A + r"33\b": ["non_compliance_breach_notification"],
    _A + r"34\b": ["non_compliance_breach_notification"],
    # Art. 35 → DPIA
    _A + r"35\b": ["insufficient_dpia"],
    # Art. 36 → prior consultation
    _A + r"36\b": ["insufficient_dpia"],
    # Art. 37-39 → DPO
    _A + r"37\b": ["insufficient_cooperation_dpa"],
    _A + r"38\b": ["insufficient_cooperation_dpa"],
    _A + r"39\b": ["insufficient_cooperation_dpa"],
    # Art. 44-49 → international transfers
    _A + r"4[4-9]\b": ["non_compliance_international_transfer"],
    # Art. 58 → DPA powers (often cooperation issues)
    _A + r"58\b": ["insufficient_cooperation_dpa"],
}

# Maps article sub-paragraphs to specific legal bases
# Handles both "Article 6(1)(a)" and "Art. 6 (1) a)" formats
_ARTICLE_TO_LEGAL_BASIS: dict[str, str] = {
    _A + r"6\s*\(1\)\s*\(?\s*a\)?": "consent",
    _A + r"6\s*\(1\)\s*\(?\s*b\)?": "contract",
    _A + r"6\s*\(1\)\s*\(?\s*c\)?": "legal_obligation",
    _A + r"6\s*\(1\)\s*\(?\s*d\)?": "vital_interest",
    _A + r"6\s*\(1\)\s*\(?\s*e\)?": "public_task",
    _A + r"6\s*\(1\)\s*\(?\s*f\)?": "legitimate_interest",
    # Art. 9(2)(a) → consent for special categories
    _A + r"9\s*\(2\)\s*\(?\s*a\)?": "consent",
    # Art. 7 implies consent was at issue
    _A + r"7\b": "consent",
}


def classify_from_articles(articles: list[str]) -> tuple[list[str], list[str]]:
    """Deterministic mapping: GDPR articles → violation_type + legal_basis_at_issue."""
    violations: set[str] = set()
    bases: set[str] = set()
    articles_str = " | ".join(articles)

    for pattern, vtypes in _ARTICLE_TO_VIOLATION.items():
        if re.search(pattern, articles_str):
            violations.update(vtypes)

    for pattern, basis in _ARTICLE_TO_LEGAL_BASIS.items():
        if re.search(pattern, articles_str):
            bases.add(basis)

    return sorted(violations), sorted(bases)


def run_classify(conn: psycopg.Connection) -> int:
    """Classify all documents that have gdpr_articles but no violation_type."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, gdpr_articles
        FROM documents
        WHERE gdpr_articles IS NOT NULL
          AND array_length(gdpr_articles, 1) > 0
          AND (violation_type IS NULL OR array_length(violation_type, 1) IS NULL)
    """)
    rows = cur.fetchall()
    log.info("Classify: %d documents to process.", len(rows))

    count = 0
    for doc_id, articles in rows:
        violations, bases = classify_from_articles(articles)
        if violations or bases:
            cur.execute("""
                UPDATE documents
                SET violation_type = %s,
                    legal_basis_at_issue = %s,
                    updated_at = now()
                WHERE id = %s
            """, (violations or None, bases or None, doc_id))
            count += 1

        if count % BATCH_SIZE == 0 and count > 0:
            log.info("Classify: %d/%d updated...", count, len(rows))

    log.info("Classify: done — %d documents enriched.", count)
    return count


# ── LLM headnote generation ──────────────────────────────────────────────────

_HEADNOTE_SYSTEM = """You are a GDPR legal editor writing headnotes for an enforcement decision database.

A headnote is a 1-2 sentence summary of ONE key legal principle from a case.
Professional legal databases (Westlaw, LexisNexis) use headnotes to help lawyers find relevant precedents.

Rules:
1. Generate 1-3 headnotes per case (one per distinct legal issue).
2. Each headnote MUST reference specific GDPR article(s).
3. Each headnote MUST be grounded in the holding text provided — do NOT add facts or principles not present.
4. Keep each headnote under 200 characters.
5. Write in English, neutral legal tone.
6. If the holding is too short or unclear to extract a principle, return fewer headnotes.

Respond ONLY with a JSON array of strings:
["headnote 1", "headnote 2"]"""

_HEADNOTE_USER = """Case: {title}
Authority: {authority}
Jurisdiction: {jurisdiction}
GDPR Articles: {articles}
Outcome: {outcome}
Fine: {fine}

=== Facts (summary) ===
{facts}

=== Holding ===
{holding}"""


def _call_haiku(client, system: str, user_msg: str) -> str:
    """Call Claude Haiku with retry."""
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            return resp.content[0].text
        except Exception as e:
            if attempt < 2:
                log.warning("Haiku retry %d: %s", attempt + 1, e)
                time.sleep(2 ** attempt)
            else:
                raise


def _parse_headnotes(raw: str) -> list[str]:
    """Parse JSON array from LLM response, tolerant of markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [h.strip() for h in result if isinstance(h, str) and len(h.strip()) > 20]
    except json.JSONDecodeError:
        log.warning("Failed to parse headnotes JSON: %s", raw[:200])
    return []


def _create_headnote_chunks(cur: psycopg.Cursor, doc_id: str, headnotes: list[str]) -> int:
    """Insert headnote chunks (section='headnote') as parent + children for retrieval.

    Follows the parent-child pattern to satisfy CHECK constraint:
    parent_id IS NOT NULL for child chunks.
    """
    # Check if headnote chunks already exist
    cur.execute(
        "SELECT count(*) FROM chunks WHERE document_id = %s AND section = 'headnote'",
        (doc_id,),
    )
    if cur.fetchone()[0] > 0:
        return 0

    # Find max chunk_index for this doc
    cur.execute(
        "SELECT COALESCE(MAX(chunk_index), -1) FROM chunks WHERE document_id = %s",
        (doc_id,),
    )
    next_idx = cur.fetchone()[0] + 1

    # Create a parent chunk with all headnotes concatenated
    parent_id = str(uuid.uuid4())
    all_headnotes = "\n\n".join(headnotes)
    cur.execute("""
        INSERT INTO chunks (id, document_id, chunk_type, parent_id, chunk_index,
                            content, content_tokens, section, search_vector)
        VALUES (%s, %s, 'parent', NULL, %s, %s, %s, 'headnote',
                to_tsvector('english', %s))
    """, (parent_id, doc_id, next_idx, all_headnotes,
          len(all_headnotes) // 4, all_headnotes))
    next_idx += 1

    # Create individual child chunks for each headnote
    count = 0
    for headnote in headnotes:
        chunk_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO chunks (id, document_id, chunk_type, parent_id, chunk_index,
                                content, content_tokens, section, search_vector)
            VALUES (%s, %s, 'child', %s, %s, %s, %s, 'headnote',
                    to_tsvector('english', %s))
        """, (chunk_id, doc_id, parent_id, next_idx, headnote,
              len(headnote) // 4, headnote))
        next_idx += 1
        count += 1

    return count + 1  # parent + children


def run_headnotes(conn: psycopg.Connection, limit: int, dry_run: bool = False) -> int:
    """Generate headnotes via Haiku for documents with holding text."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    cur = conn.cursor()
    limit_sql = f"LIMIT {limit}" if limit > 0 else ""
    cur.execute(f"""
        SELECT id, title, authority, jurisdiction, outcome,
               fine_amount, gdpr_articles,
               summary_facts, summary_holding
        FROM documents
        WHERE summary_holding IS NOT NULL
          AND length(summary_holding) > 100
          AND (headnotes IS NULL OR array_length(headnotes, 1) IS NULL)
        ORDER BY fine_amount DESC NULLS LAST
        {limit_sql}
    """)
    rows = cur.fetchall()
    log.info("Headnotes: %d documents to process%s.",
             len(rows), f" (limit={limit})" if limit else "")

    if dry_run:
        log.info("--dry-run: no API calls.")
        return 0

    count = 0
    total_chunks = 0
    t_start = time.monotonic()

    for doc_id, title, authority, jurisdiction, outcome, fine, articles, facts, holding in rows:
        articles_str = ", ".join(articles) if articles else "Not specified"
        fine_str = f"EUR {fine:,}" if fine else "None"
        facts_trunc = (facts or "")[:2000]
        holding_trunc = (holding or "")[:4000]

        user_msg = _HEADNOTE_USER.format(
            title=title, authority=authority or "Unknown",
            jurisdiction=jurisdiction or "Unknown",
            articles=articles_str, outcome=outcome or "Unknown",
            fine=fine_str, facts=facts_trunc, holding=holding_trunc,
        )

        try:
            raw = _call_haiku(client, _HEADNOTE_SYSTEM, user_msg)
            headnotes = _parse_headnotes(raw)
        except Exception as e:
            log.warning("Doc %s (%s): LLM error — %s", doc_id, title[:40], e)
            continue

        if not headnotes:
            log.warning("Doc %s (%s): no valid headnotes generated.", doc_id, title[:40])
            continue

        # Update document
        cur.execute("""
            UPDATE documents SET headnotes = %s, updated_at = now() WHERE id = %s
        """, (headnotes, doc_id))

        # Create headnote chunks
        n_chunks = _create_headnote_chunks(cur, doc_id, headnotes)
        total_chunks += n_chunks
        count += 1

        if count % 10 == 0:
            elapsed = time.monotonic() - t_start
            rate = count / elapsed if elapsed > 0 else 0
            log.info("Headnotes: %d/%d docs | %d chunks | %.1f docs/s",
                     count, len(rows), total_chunks, rate)

        time.sleep(0.1)  # rate limit courtesy

    elapsed = time.monotonic() - t_start
    log.info("Headnotes: done — %d docs enriched, %d chunks created (%.0fs).",
             count, total_chunks, elapsed)
    return count


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Legal metadata enrichment")
    parser.add_argument("--mode", choices=["classify", "headnotes", "all"],
                        default="all", help="Enrichment mode")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit docs for headnotes (0 = all, 20 = test)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count candidates without processing")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL not set.")
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    if args.mode in ("classify", "all"):
        run_classify(conn)

    if args.mode in ("headnotes", "all"):
        run_headnotes(conn, limit=args.limit, dry_run=args.dry_run)

    conn.close()


if __name__ == "__main__":
    main()
