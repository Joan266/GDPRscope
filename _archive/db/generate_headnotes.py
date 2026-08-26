"""
Generate headnote chunks via LLM from holding text.

For each document with holding content, extracts 1-3 key legal principles
using Haiku and inserts them as child chunks (section='headnote') pointing
to the holding parent. These chunks are optimized for conceptual query matching.

Idempotent: skips documents that already have headnote chunks.

Usage:
    PYTHONUTF8=1 python db/generate_headnotes.py
    PYTHONUTF8=1 python db/generate_headnotes.py --limit 100 --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid

import openai
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL_ID = "anthropic/claude-haiku-4-5"
BATCH_SIZE = 20  # docs per commit

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

_EXTRACT_PROMPT = """You are a legal analyst. Given a GDPR enforcement decision's holding/reasoning section, extract 1-3 key legal principles as standalone sentences.

Rules:
- Each principle must be a complete, self-contained sentence
- Focus on the LEGAL RULE applied, not the specific facts
- Include the GDPR article when mentioned
- Write in English regardless of source language
- Keep each principle under 200 characters
- Output ONLY a JSON array of strings, nothing else

Example output:
["Consent under Art. 6(1)(a) is invalid in employment contexts due to the inherent power imbalance between employer and employee.", "A Data Protection Impact Assessment under Art. 35 is mandatory before deploying systematic video surveillance in public areas."]

Holding text:
{holding_text}"""


def fetch_docs_needing_headnotes(
    cur: psycopg.Cursor, limit: int
) -> list[dict]:
    """Fetch documents with holding parents but no headnote chunks."""
    cur.execute("""
        SELECT p.id AS parent_id, p.document_id, p.content,
               d.title, d.gdpr_articles
        FROM   chunks p
        JOIN   documents d ON d.id = p.document_id
        WHERE  p.chunk_type = 'parent'
          AND  p.section    = 'holding'
          AND  length(p.content) > 100
          AND  NOT EXISTS (
               SELECT 1 FROM chunks h
               WHERE h.document_id = p.document_id
                 AND h.section = 'headnote'
          )
        ORDER BY d.fine_amount DESC NULLS LAST, p.created_at
        LIMIT  %s
    """, (limit,))
    return [
        {
            "parent_id": str(row[0]),
            "document_id": str(row[1]),
            "content": row[2],
            "title": row[3],
            "gdpr_articles": row[4] or [],
        }
        for row in cur.fetchall()
    ]


def extract_headnotes(client: openai.OpenAI, holding_text: str) -> list[str]:
    """Call LLM via OpenRouter to extract legal principles from holding text."""
    text = holding_text[:3000]
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            max_tokens=400,
            temperature=0.0,
            messages=[{
                "role": "user",
                "content": _EXTRACT_PROMPT.format(holding_text=text),
            }],
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        principles = json.loads(raw)
        if isinstance(principles, list):
            return [p for p in principles if isinstance(p, str) and len(p) > 20]
        return []
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("Failed to extract headnotes: %s", exc)
        return []


def insert_headnote_chunks(
    cur: psycopg.Cursor,
    document_id: str,
    parent_id: str,
    headnotes: list[str],
) -> int:
    """Insert headnote child chunks pointing to the holding parent."""
    inserted = 0
    for i, text in enumerate(headnotes):
        chunk_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO chunks (id, document_id, content, chunk_type, section,
                                chunk_index, parent_id)
            VALUES (%s, %s, %s, 'child', 'headnote', %s, %s)
            ON CONFLICT DO NOTHING
        """, (chunk_id, document_id, text, i, parent_id))
        inserted += 1
    return inserted


def run(args: argparse.Namespace) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY not set")
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

    with conn.cursor() as cur:
        docs = fetch_docs_needing_headnotes(cur, args.limit)
    log.info("Found %d documents needing headnotes", len(docs))

    if not docs:
        log.info("Nothing to do.")
        conn.close()
        return

    if args.dry_run:
        log.info("--dry-run: would generate headnotes for %d docs", len(docs))
        for d in docs[:5]:
            log.info("  %s (%d chars)", d["title"][:60], len(d["content"]))
        conn.close()
        return

    n_docs = 0
    n_chunks = 0
    n_err = 0
    t_start = time.monotonic()

    with conn.cursor() as cur:
        for i, doc in enumerate(docs):
            try:
                headnotes = extract_headnotes(client, doc["content"])
                if headnotes:
                    inserted = insert_headnote_chunks(
                        cur, doc["document_id"], doc["parent_id"], headnotes,
                    )
                    n_chunks += inserted
                    n_docs += 1
                else:
                    log.debug("No headnotes for %s", doc["title"][:50])
            except Exception as exc:
                log.warning("Error on doc %s: %s", doc["document_id"], exc)
                n_err += 1
                time.sleep(1)  # back off on errors

            if (i + 1) % 50 == 0:
                elapsed = time.monotonic() - t_start
                rate = (i + 1) / elapsed
                remaining = (len(docs) - i - 1) / rate if rate > 0 else 0
                log.info(
                    "Progress: %d/%d docs | %d chunks | %d errors | %.1f docs/s | ETA: %.0f min",
                    i + 1, len(docs), n_chunks, n_err, rate, remaining / 60,
                )

    elapsed = time.monotonic() - t_start
    conn.close()

    log.info("=" * 55)
    log.info("Completed in %.1f min", elapsed / 60)
    log.info("  Documents processed: %d", n_docs)
    log.info("  Headnote chunks created: %d", n_chunks)
    log.info("  Errors: %d", n_err)
    log.info("Next: python db/embed.py --sections headnote")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate headnote chunks via LLM")
    parser.add_argument("--limit", type=int, default=5000,
                        help="Max documents to process (default: 5000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count docs without calling LLM")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
