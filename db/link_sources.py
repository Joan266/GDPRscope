"""
JurisMind — Cross-source linking: Tracker → GDPRhub

Matches Enforcement Tracker documents to their GDPRhub equivalents
by controller_name + jurisdiction. Sets canonical_id on the Tracker doc
pointing to the GDPRhub version (which has full facts + holding).

When a Tracker doc has a canonical_id, the RAG can swap the shallow
Tracker card for the full GDPRhub context at retrieval time.

Usage:
    PYTHONUTF8=1 python db/link_sources.py
    PYTHONUTF8=1 python db/link_sources.py --dry-run
"""

import argparse
import logging
import os
import sys

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def ensure_column(conn: psycopg.Connection) -> None:
    """Add canonical_id column if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'documents' AND column_name = 'canonical_id'
        """)
        if not cur.fetchone():
            log.info("Adding canonical_id column...")
            cur.execute(
                "ALTER TABLE documents ADD COLUMN canonical_id UUID REFERENCES documents(id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_doc_canonical ON documents (canonical_id) "
                "WHERE canonical_id IS NOT NULL"
            )
            log.info("canonical_id column added.")


def find_matches(cur: psycopg.Cursor) -> list[tuple[str, str, str, str, int]]:
    """Find Tracker docs that have a GDPRhub equivalent.

    Match criteria: same controller_name + jurisdiction (case-insensitive).
    When multiple GDPRhub matches exist, pick the one with the most content.
    """
    cur.execute("""
        SELECT DISTINCT ON (t.id)
               t.id AS tracker_id,
               t.source_id AS tracker_sid,
               g.id AS gdprhub_id,
               g.source_id AS gdprhub_sid,
               length(coalesce(g.summary_facts, ''))
                 + length(coalesce(g.summary_holding, '')) AS gdprhub_depth
        FROM documents t
        JOIN documents g
          ON lower(t.controller_name) = lower(g.controller_name)
         AND lower(t.jurisdiction) = lower(g.jurisdiction)
        WHERE t.source = 'enforcement_tracker'
          AND g.source = 'gdprhub'
          AND t.controller_name IS NOT NULL
          AND t.canonical_id IS NULL
        ORDER BY t.id,
                 length(coalesce(g.summary_facts, ''))
                   + length(coalesce(g.summary_holding, '')) DESC
    """)
    return cur.fetchall()


def link_sources(conn: psycopg.Connection, dry_run: bool = False) -> int:
    """Set canonical_id on Tracker docs pointing to GDPRhub equivalents."""
    with conn.cursor() as cur:
        matches = find_matches(cur)
        log.info("Found %d Tracker docs with GDPRhub equivalent.", len(matches))

        if dry_run:
            for tracker_id, t_sid, gdprhub_id, g_sid, depth in matches[:10]:
                log.info("  Tracker #%s → %s (depth=%d)", t_sid, g_sid, depth)
            if len(matches) > 10:
                log.info("  ... and %d more", len(matches) - 10)
            log.info("--dry-run: no changes written.")
            return 0

        count = 0
        for tracker_id, t_sid, gdprhub_id, g_sid, depth in matches:
            cur.execute(
                "UPDATE documents SET canonical_id = %s, updated_at = now() WHERE id = %s",
                (gdprhub_id, tracker_id),
            )
            count += 1

        log.info("Linked %d Tracker docs to GDPRhub equivalents.", count)
        return count


def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Cross-source linking")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show matches without writing")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL not set.")
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    ensure_column(conn)
    link_sources(conn, dry_run=args.dry_run)
    conn.close()


if __name__ == "__main__":
    main()
