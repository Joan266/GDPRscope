"""
Re-chunk documents with the new semantic chunker (additive mode).

Generates new chunks alongside existing ones — does NOT delete old chunks.
New chunks use sections: holding_summary, holding_decision (from split schema).
Old chunks keep section: holding (from original monolithic schema).

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python db/rechunk.py [--source gdprhub|all] [--dry-run]
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from chunker import make_chunks

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_SIZE = 50

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Sections produced by the new chunker
NEW_SECTIONS = ('holding_summary', 'holding_decision')


def rechunk_documents(conn: psycopg.Connection, source: str, dry_run: bool = False) -> None:
    """Generate new chunks alongside old ones for given source."""

    where = "WHERE d.source = %s" if source != "all" else ""
    params = [source] if source != "all" else []

    with conn.cursor() as cur:
        # Skip docs that already have new-style chunks
        cur.execute(f"""
            SELECT COUNT(DISTINCT d.id) FROM documents d {where}
        """, params)
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT COUNT(DISTINCT d.id) FROM documents d
            JOIN chunks c ON c.document_id = d.id AND c.section IN ('holding_summary', 'holding_decision')
            {where}
        """, params)
        already_done = cur.fetchone()[0]

        todo = total - already_done
        log.info("Total docs: %d, already re-chunked: %d, to process: %d", total, already_done, todo)

        if dry_run:
            cur.execute(f"""
                SELECT d.id, d.title, d.source
                FROM documents d
                WHERE d.id NOT IN (
                    SELECT DISTINCT document_id FROM chunks WHERE section IN ('holding_summary', 'holding_decision')
                )
                {"AND d.source = %s" if source != "all" else ""}
                LIMIT 5
            """, params)
            for doc_id, title, src in cur.fetchall():
                doc_data = _load_doc_data(cur, doc_id)
                new_chunks = make_chunks(str(doc_id), doc_data)
                new_sections = set(c["section"] for c in new_chunks)
                log.info("  [DRY] %s: %d new chunks, sections=%s", title[:60], len(new_chunks), new_sections)
            return

        # Process documents that don't have new-style chunks yet
        cur.execute(f"""
            SELECT d.id, d.title, d.source FROM documents d
            WHERE d.id NOT IN (
                SELECT DISTINCT document_id FROM chunks WHERE section IN ('holding_summary', 'holding_decision')
            )
            {"AND d.source = %s" if source != "all" else ""}
            ORDER BY d.id
        """, params)
        docs = cur.fetchall()

        processed = 0
        new_total = 0
        errors = 0

        for doc_id, title, src in docs:
            try:
                doc_data = _load_doc_data(cur, doc_id)
                new_chunks = make_chunks(str(doc_id), doc_data)

                # Only insert chunks for NEW sections (holding_summary, holding_decision)
                # Skip facts/dispute/teaser — those already exist from old chunker
                new_section_chunks = [c for c in new_chunks if c["section"] in NEW_SECTIONS]

                if new_section_chunks:
                    cur.executemany(
                        """INSERT INTO chunks (id, document_id, chunk_type, parent_id, chunk_index,
                                              content, content_tokens, section, search_vector)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', %s))
                           ON CONFLICT (id) DO NOTHING""",
                        [(c["id"], c["document_id"], c["chunk_type"], c["parent_id"],
                          c["chunk_index"], c["content"], c["content_tokens"], c["section"],
                          c["content"])
                         for c in new_section_chunks],
                    )
                    new_total += len(new_section_chunks)

                processed += 1
                if processed % BATCH_SIZE == 0:
                    conn.commit()
                    log.info("  %d/%d docs processed, %d new chunks...", processed, len(docs), new_total)

            except Exception as e:
                log.warning("Error re-chunking '%s': %s", title[:60], e)
                conn.rollback()
                errors += 1

        conn.commit()

        log.info("=" * 60)
        log.info("Re-chunking complete (additive mode):")
        log.info("  Documents processed: %d (%d errors)", processed, errors)
        log.info("  New chunks added: %d", new_total)
        log.info("  Old chunks: untouched")
        log.info("  Next step: embed new chunks with BGE-M3")


def _load_doc_data(cur: psycopg.Cursor, doc_id: str) -> dict:
    """Load document fields needed by the chunker."""
    cur.execute("""
        SELECT source, title, jurisdiction, authority, fine_amount, sector,
               summary_teaser, summary_facts, summary_dispute, summary_holding,
               gdpr_articles, case_number, ecli, celex, decision_date,
               holding_full_text
        FROM documents WHERE id = %s
    """, (str(doc_id),))
    row = cur.fetchone()
    return {
        "source":            row[0],
        "title":             row[1],
        "jurisdiction":      row[2],
        "authority":         row[3],
        "fine_amount":       row[4],
        "sector":            row[5],
        "summary_teaser":    row[6],
        "summary_facts":     row[7],
        "summary_dispute":   row[8],
        "summary_holding":   row[9],
        "gdpr_articles":     row[10] or [],
        "case_number":       row[11],
        "ecli":              row[12],
        "celex":             row[13],
        "decision_date":     row[14],
        "holding_full_text": row[15],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-chunk documents (additive — keeps old chunks)")
    parser.add_argument("--source", choices=["all", "gdprhub", "tracker", "eurlex"],
                        default="all", help="Source to re-chunk (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying DB")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL)
    try:
        rechunk_documents(conn, args.source, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
