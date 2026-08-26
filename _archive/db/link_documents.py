"""
Populate document_links by cross-referencing cases across sources.

Strategy: jurisdiction + decision_date + fuzzy controller_name.
Both docs MUST have a non-null decision_date — date mismatch = different case.

Usage:
    PYTHONUTF8=1 python db/link_documents.py [--dry-run]
"""

import argparse
import logging
import os
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MIN_CONFIDENCE = 0.60


def _ordered_pair(a: str, b: str) -> tuple[str, str]:
    """Return (doc_a, doc_b) with doc_a < doc_b for the CHECK constraint."""
    return (a, b) if a < b else (b, a)


_NOISE_WORDS = {
    "municipality", "commune", "comune", "city", "town", "region",
    "azienda", "authority", "association", "ministry", "department",
    "di", "de", "del", "della", "des", "van", "von", "the", "of",
    "s.r.l.", "s.p.a.", "s.l.", "s.a.", "s.a.u.", "ltd", "limited",
    "inc", "gmbh", "ag", "ab", "srl", "spa", "sl", "sa", "sas",
    "data", "protection", "private", "public",
}


def _is_false_positive(ctrl_a: str, ctrl_b: str) -> bool:
    """Heuristic to catch common false positives where SequenceMatcher
    scores high but the entities are clearly different."""
    a, b = ctrl_a.lower().strip(), ctrl_b.lower().strip()

    # Strip common suffixes that inflate similarity
    for suffix in [" s.r.l.", " s.p.a.", " s.l.", " s.a.", " s.a.u.",
                   " ltd", " limited", " inc", " gmbh", " ag", " ab",
                   " srl", " spa", " sl"]:
        a = a.removesuffix(suffix)
        b = b.removesuffix(suffix)

    words_a = set(a.split()) - _NOISE_WORDS
    words_b = set(b.split()) - _NOISE_WORDS
    if not words_a or not words_b:
        return True

    # At least one meaningful word must appear in both
    if not words_a & words_b:
        # Fallback: check if first meaningful word of one is substring of the other
        w1 = next(iter(words_a))
        w2 = next(iter(words_b))
        if w1 not in b and w2 not in a:
            return True

    return False


def apply_migration(conn: psycopg.Connection) -> None:
    """Create document_links table if it doesn't exist."""
    migration_path = Path(__file__).resolve().parent / "migrations" / "002_document_links.sql"
    sql = migration_path.read_text(encoding="utf-8")
    conn.execute(sql)
    conn.commit()
    log.info("Migration applied: document_links table ready")


def _load_docs(cur: psycopg.Cursor, source: str) -> list[tuple]:
    """Load docs with the fields needed for matching."""
    cur.execute("""
        SELECT id, jurisdiction, decision_date, controller_name, title
        FROM documents
        WHERE source = %s
          AND decision_date IS NOT NULL
          AND controller_name IS NOT NULL
          AND controller_name != ''
    """, (source,))
    return cur.fetchall()


def _build_index(docs: list[tuple]) -> dict[tuple, list[tuple]]:
    """Index docs by (jurisdiction, date) for fast lookup."""
    idx: dict[tuple, list[tuple]] = defaultdict(list)
    for row in docs:
        key = (row[1], str(row[2]))
        idx[key].append(row)
    return idx


def _match_sources(
    source_a: list[tuple],
    index_b: dict[tuple, list[tuple]],
) -> list[tuple[str, str, float]]:
    """Match docs from source_a against indexed source_b.
    Returns list of (doc_a_id, doc_b_id, confidence)."""
    links: list[tuple[str, str, float]] = []
    for a_id, a_jur, a_date, a_ctrl, a_title in source_a:
        key = (a_jur, str(a_date))
        for b_id, _, _, b_ctrl, b_title in index_b.get(key, []):
            ratio = SequenceMatcher(None, a_ctrl.lower(), b_ctrl.lower()).ratio()
            if ratio >= MIN_CONFIDENCE and not _is_false_positive(a_ctrl, b_ctrl):
                a, b = _ordered_pair(str(a_id), str(b_id))
                links.append((a, b, round(ratio, 3)))
    return links


def match_all(conn: psycopg.Connection, dry_run: bool = False) -> int:
    """Match documents across all source pairs."""
    cur = conn.cursor()

    gdprhub_docs = _load_docs(cur, "gdprhub")
    tracker_docs = _load_docs(cur, "enforcement_tracker")
    edpb_docs = _load_docs(cur, "edpb_oss")

    log.info("Candidates: %d GDPRhub, %d Tracker, %d EDPB",
             len(gdprhub_docs), len(tracker_docs), len(edpb_docs))

    tracker_idx = _build_index(tracker_docs)
    edpb_idx = _build_index(edpb_docs)
    gdprhub_idx = _build_index(gdprhub_docs)

    all_links: list[tuple[str, str, float]] = []
    all_links.extend(_match_sources(gdprhub_docs, tracker_idx))
    all_links.extend(_match_sources(gdprhub_docs, edpb_idx))
    all_links.extend(_match_sources(tracker_docs, edpb_idx))

    # Deduplicate, keep highest confidence
    best: dict[tuple[str, str], float] = {}
    for a, b, conf in all_links:
        if (a, b) not in best or conf > best[(a, b)]:
            best[(a, b)] = conf

    log.info("Found %d links (min confidence %.2f)", len(best), MIN_CONFIDENCE)

    if dry_run:
        for (a, b), conf in list(best.items())[:15]:
            cur.execute("SELECT source, title FROM documents WHERE id = %s", (a,))
            r1 = cur.fetchone()
            cur.execute("SELECT source, title FROM documents WHERE id = %s", (b,))
            r2 = cur.fetchone()
            log.info("  [%.2f] %s <%s> <-> %s <%s>",
                     conf, r1[0], r1[1][:50], r2[0], r2[1][:50])
        return len(best)

    inserted = 0
    for (a, b), conf in best.items():
        try:
            cur.execute("""
                INSERT INTO document_links (doc_a, doc_b, link_type, confidence)
                VALUES (%s::UUID, %s::UUID, 'same_case', %s)
                ON CONFLICT (doc_a, doc_b) DO UPDATE
                    SET confidence = GREATEST(document_links.confidence, EXCLUDED.confidence)
            """, (a, b, conf))
            inserted += cur.rowcount
        except Exception as exc:
            log.warning("Skip link %s <-> %s: %s", a[:8], b[:8], exc)

    conn.commit()
    log.info("Inserted/updated %d links", inserted)
    return inserted


def print_stats(conn: psycopg.Connection) -> None:
    """Show summary of document_links."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM document_links")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT link_type, COUNT(*),
               ROUND(AVG(confidence)::numeric, 2),
               MIN(confidence), MAX(confidence)
        FROM document_links
        GROUP BY link_type
    """)
    print(f"\n{'='*60}")
    print(f"document_links: {total} total")
    print(f"{'='*60}")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} links (avg conf={row[2]}, min={row[3]:.2f}, max={row[4]:.2f})")

    cur.execute("""
        SELECT d1.source, d2.source, COUNT(*)
        FROM document_links dl
        JOIN documents d1 ON d1.id = dl.doc_a
        JOIN documents d2 ON d2.id = dl.doc_b
        GROUP BY d1.source, d2.source
        ORDER BY COUNT(*) DESC
    """)
    print("\nCross-source links:")
    for row in cur.fetchall():
        print(f"  {row[0]} <-> {row[1]}: {row[2]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Link documents across sources")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL)

    try:
        apply_migration(conn)
        n = match_all(conn, dry_run=args.dry_run)
        log.info("Done: %d links", n)

        if not args.dry_run:
            print_stats(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
