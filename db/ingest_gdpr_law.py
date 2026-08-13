"""
JurisMind — Ingest GDPR law text (99 articles + 173 recitals)

Source: github.com/6022-labs/gdpr-mcp-server (CC0 license)
Structured JSON: chapters, articles with paragraphs, recitals.

Creates tables gdpr_law + gdpr_recitals and populates them.
Idempotent: ON CONFLICT DO UPDATE.

Usage:
    DATABASE_URL=... python db/ingest_gdpr_law.py
    DATABASE_URL=... python db/ingest_gdpr_law.py --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time

import psycopg
import requests

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BASE_URL = "https://raw.githubusercontent.com/6022-labs/gdpr-mcp-server/main/data/v1"
NUM_ARTICLES = 99
NUM_RECITALS = 173
NUM_CHAPTERS = 11
REQUEST_DELAY = 0.15  # be nice to GitHub

HEADERS = {"User-Agent": "JurisMind/1.0 (research@jurismind.dev)"}

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# ── Chapter mapping ────────────────────────────────────────────────────────────

CHAPTER_MAP: dict[int, dict] = {}  # article_number -> {chapter_number, chapter_title}


def fetch_json(url: str) -> dict | None:
    """Fetch JSON from URL with retry."""
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt == 2:
                log.warning("Failed to fetch %s: %s", url, e)
                return None
            time.sleep(1)
    return None


def load_chapters() -> None:
    """Load chapter metadata and build article->chapter mapping."""
    log.info("Loading %d chapters...", NUM_CHAPTERS)
    for ch_num in range(1, NUM_CHAPTERS + 1):
        url = f"{BASE_URL}/chapters/ch-{ch_num}.json"
        data = fetch_json(url)
        if not data:
            log.warning("Chapter %d not found", ch_num)
            continue
        chapter_title = f"Chapter {data['roman']} - {data['title']}"
        for art_id in data.get("articles_ids", []):
            art_num = int(art_id.replace("art-", ""))
            CHAPTER_MAP[art_num] = {
                "chapter_number": ch_num,
                "chapter_title": chapter_title,
            }
        time.sleep(REQUEST_DELAY)
    log.info("Chapter mapping built: %d articles mapped", len(CHAPTER_MAP))


def fetch_article(art_num: int) -> dict | None:
    """Fetch article metadata + all paragraphs, return assembled dict."""
    art_url = f"{BASE_URL}/articles/art-{art_num}/art.json"
    art_data = fetch_json(art_url)
    if not art_data:
        return None

    title = art_data["title"]
    num_paragraphs = art_data.get("number_of_paragraphs", 0)

    # Fetch all paragraphs
    paragraphs: list[str] = []
    for p_num in range(1, num_paragraphs + 1):
        para_url = f"{BASE_URL}/articles/art-{art_num}/para-{p_num}.json"
        para_data = fetch_json(para_url)
        if not para_data:
            continue
        texts = para_data.get("texts", [])
        para_text = "\n".join(texts)
        paragraphs.append(f"({p_num}) {para_text}")
        time.sleep(REQUEST_DELAY)

    full_text = "\n\n".join(paragraphs)
    chapter_info = CHAPTER_MAP.get(art_num, {})

    return {
        "article_number": str(art_num),
        "article_title": title,
        "chapter": chapter_info.get("chapter_title"),
        "full_text": full_text,
    }


def fetch_recital(rec_num: int) -> dict | None:
    """Fetch a single recital."""
    url = f"{BASE_URL}/recitals/rec-{rec_num}.json"
    data = fetch_json(url)
    if not data:
        return None
    texts = data.get("texts", [])
    return {
        "recital_number": rec_num,
        "full_text": "\n".join(texts),
    }


# ── DDL ────────────────────────────────────────────────────────────────────────

DDL_GDPR_LAW = """
CREATE TABLE IF NOT EXISTS gdpr_law (
    article_number  TEXT PRIMARY KEY,
    article_title   TEXT NOT NULL,
    chapter         TEXT,
    full_text       TEXT NOT NULL,
    search_vector   TSVECTOR,
    embedding       VECTOR(1024)
);
"""

DDL_GDPR_RECITALS = """
CREATE TABLE IF NOT EXISTS gdpr_recitals (
    recital_number  INT PRIMARY KEY,
    full_text       TEXT NOT NULL,
    search_vector   TSVECTOR,
    embedding       VECTOR(1024)
);
"""


def ensure_tables(conn: psycopg.Connection) -> None:
    """Create tables if they don't exist."""
    conn.execute(DDL_GDPR_LAW)
    conn.execute(DDL_GDPR_RECITALS)
    log.info("Tables gdpr_law and gdpr_recitals ensured")


# ── Upsert ─────────────────────────────────────────────────────────────────────

UPSERT_ARTICLE = """
INSERT INTO gdpr_law (article_number, article_title, chapter, full_text, search_vector)
VALUES (
    %(article_number)s,
    %(article_title)s,
    %(chapter)s,
    %(full_text)s,
    to_tsvector('english', %(article_title)s || ' ' || %(full_text)s)
)
ON CONFLICT (article_number) DO UPDATE SET
    article_title = EXCLUDED.article_title,
    chapter       = EXCLUDED.chapter,
    full_text     = EXCLUDED.full_text,
    search_vector = EXCLUDED.search_vector
"""

UPSERT_RECITAL = """
INSERT INTO gdpr_recitals (recital_number, full_text, search_vector)
VALUES (
    %(recital_number)s,
    %(full_text)s,
    to_tsvector('english', %(full_text)s)
)
ON CONFLICT (recital_number) DO UPDATE SET
    full_text     = EXCLUDED.full_text,
    search_vector = EXCLUDED.search_vector
"""


def ingest_articles(conn: psycopg.Connection, dry_run: bool = False) -> int:
    """Fetch and upsert all 99 GDPR articles."""
    log.info("Fetching %d articles...", NUM_ARTICLES)
    count = 0
    for art_num in range(1, NUM_ARTICLES + 1):
        article = fetch_article(art_num)
        if not article:
            log.warning("Article %d: not found, skipping", art_num)
            continue
        if dry_run:
            log.info("Article %d: %s (%d chars)", art_num, article["article_title"], len(article["full_text"]))
        else:
            conn.execute(UPSERT_ARTICLE, article)
            log.info("Article %d: %s", art_num, article["article_title"])
        count += 1
    return count


def ingest_recitals(conn: psycopg.Connection, dry_run: bool = False) -> int:
    """Fetch and upsert all 173 GDPR recitals."""
    log.info("Fetching %d recitals...", NUM_RECITALS)
    count = 0
    for rec_num in range(1, NUM_RECITALS + 1):
        recital = fetch_recital(rec_num)
        if not recital:
            log.warning("Recital %d: not found, skipping", rec_num)
            continue
        if dry_run:
            log.info("Recital %d: %d chars", rec_num, len(recital["full_text"]))
        else:
            conn.execute(UPSERT_RECITAL, recital)
        count += 1
        if rec_num % 20 == 0:
            log.info("Recitals progress: %d/%d", rec_num, NUM_RECITALS)
    return count


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest GDPR law text into CockroachDB")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and display without inserting")
    parser.add_argument("--articles-only", action="store_true", help="Only ingest articles")
    parser.add_argument("--recitals-only", action="store_true", help="Only ingest recitals")
    args = parser.parse_args()

    if not args.dry_run and not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    # 1. Load chapter mapping (needed for articles)
    if not args.recitals_only:
        load_chapters()

    if args.dry_run:
        if not args.recitals_only:
            ingest_articles(None, dry_run=True)  # type: ignore[arg-type]
        if not args.articles_only:
            ingest_recitals(None, dry_run=True)  # type: ignore[arg-type]
        return

    # 2. Connect and ingest
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    try:
        ensure_tables(conn)

        if not args.recitals_only:
            art_count = ingest_articles(conn)
            log.info("Articles done: %d inserted/updated", art_count)

        if not args.articles_only:
            rec_count = ingest_recitals(conn)
            log.info("Recitals done: %d inserted/updated", rec_count)

        # Summary
        row = conn.execute("SELECT count(*) FROM gdpr_law").fetchone()
        log.info("Total articles in gdpr_law: %d", row[0])
        row = conn.execute("SELECT count(*) FROM gdpr_recitals").fetchone()
        log.info("Total recitals in gdpr_recitals: %d", row[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
