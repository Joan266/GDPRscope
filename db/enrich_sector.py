"""
JurisMind — Sector enrichment via GDPR Enforcement Tracker cross-reference

GDPRhub API does not provide a 'sector' field. This script downloads the
Enforcement Tracker dataset (3,200+ cases) and matches records to our GDPRhub
documents by (authority, year, fine_amount), then backfills the sector column.

Matching logic (confidence order):
  1. PRIMARY  — (auth_key, year, fine_amount) exact match  → high confidence
  2. SECONDARY — (auth_key, fine_amount) when year is NULL in GDPRhub → medium

Authority key normalization:
  GDPRhub:  "AEPD (Spain)"                             → "AEPD"
  Tracker:  "Spanish Data Protection Authority (aepd)" → "AEPD"

Usage:
    python db/enrich_sector.py              # all GDPRhub docs with sector=NULL
    python db/enrich_sector.py --dry-run    # show matches without writing
    python db/enrich_sector.py --limit 200  # cap at 200 docs
    python db/enrich_sector.py --cache      # use cached tracker data if available
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import psycopg
import requests

DATABASE_URL  = os.environ.get("DATABASE_URL", "")
TRACKER_URL   = "https://www.enforcementtracker.com/"
CACHE_PATH    = Path(__file__).parent.parent / "data" / "tracker_full.json"
REQUEST_DELAY = 0.5
HEADERS       = {"User-Agent": "JurisMind/1.0 (research@jurismind.dev)"}

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Authority key normalization ─────────────────────────────────────────────────

def authority_key(authority: str) -> str:
    """Normalize authority string to a short comparable key (abbreviation).

    GDPRhub format:  "AEPD (Spain)"                              → "AEPD"
    GDPRhub format:  "APD/GBA (Belgium)"                         → "APD"
    Tracker format:  "Spanish Data Protection Authority (aepd)"  → "AEPD"
    Tracker format:  "Belgian Data Protection Authority (APD)"   → "APD"
    """
    if not authority:
        return ""
    m = re.search(r'^(.+?)\s*\(([^)]+)\)', authority.strip())
    if not m:
        key = authority.strip().upper()[:15]
    else:
        pre  = m.group(1).strip()
        post = m.group(2).strip()

        # GDPRhub: pre is short + uppercase → it's the abbreviation ("AEPD", "APD/GBA")
        if len(pre) <= 10 and (pre.upper() == pre or "/" in pre):
            key = pre.upper()
        else:
            # Tracker: post is the abbreviation in lowercase ("aepd", "apd/gba", "dsb")
            key = post.upper()

    # Normalise dual-language names: "APD/GBA" → "APD" (Tracker only stores first)
    return key.split("/")[0] if "/" in key else key


# ── Enforcement Tracker download ─────────────────────────────────────────────────

def fetch_tracker(use_cache: bool = False) -> list[dict]:
    """Download Enforcement Tracker JSON embedded in page HTML."""
    if use_cache and CACHE_PATH.exists():
        log.info("Tracker: loading from cache %s", CACHE_PATH)
        return json.loads(CACHE_PATH.read_text("utf-8"))

    log.info("Tracker: downloading from %s ...", TRACKER_URL)
    r = requests.get(TRACKER_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text
    log.info("Tracker: %d bytes received. Extracting JSON...", len(html))

    patterns = [
        r'var\s+\w*[Dd]ata\w*\s*=\s*(\[.*?\])\s*;',
        r'var\s+\w*[Cc]ases\w*\s*=\s*(\[.*?\])\s*;',
        r'=\s*(\[\s*\{"e"\s*:\s*1\b.*?\])\s*[,;]',
        r'(\[\s*\{"e"\s*:\s*1\b.*?\])',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                records = json.loads(match.group(1))
                log.info("Tracker: %d records found.", len(records))
                if use_cache:
                    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    CACHE_PATH.write_text(json.dumps(records, ensure_ascii=False), "utf-8")
                    log.info("Tracker: cached to %s", CACHE_PATH)
                return records
            except json.JSONDecodeError:
                continue

    log.error("Tracker: could not extract embedded JSON from page.")
    return []


# ── Index building ────────────────────────────────────────────────────────────────

def build_tracker_index(records: list[dict]) -> dict:
    """Build lookup structures for matching.

    Returns:
        {
          "by_fine":  {(auth_key, year, fine): [sector, ...]},
          "by_year":  {(auth_key, year): [(fine, sector), ...]},  # for fuzzy fine match
        }
    """
    by_fine: dict[tuple, list[str]]       = defaultdict(list)
    by_year: dict[tuple, list[tuple]]     = defaultdict(list)

    for r in records:
        auth   = authority_key(r.get("a", ""))
        year   = r.get("y")
        fine   = r.get("f")
        sector = (r.get("s") or "").strip()

        if not auth or not sector or sector == "Not assigned":
            continue

        if fine and fine > 0 and year:
            by_fine[(auth, int(year), int(fine))].append(sector)
            by_year[(auth, int(year))].append((int(fine), sector))

    return {"by_fine": dict(by_fine), "by_year": dict(by_year)}


FINE_TOLERANCE = 0.06  # 6% tolerance for fine amount matching


def lookup_sector(idx: dict, auth: str, year: int | None, fine: int | None) -> tuple[str | None, str]:
    """Try to find a unique sector for the given (auth, year, fine) combination.

    Returns (sector | None, tier_label).
    Tiers (most → least confident):
      PRIMARY  — exact (auth, year, fine) match
      FUZZY    — (auth, year) + fine within ±6% → unique sector
    """
    key_auth = authority_key(auth)
    if not key_auth:
        return None, "miss"

    # PRIMARY: exact (auth, year, fine) match
    if year and fine:
        sectors = idx["by_fine"].get((key_auth, int(year), int(fine)), [])
        unique  = list(set(sectors))
        if len(unique) == 1:
            return unique[0], "primary"

    # FUZZY: (auth, year) + fine within ±FINE_TOLERANCE → unique sector
    if year and fine:
        candidates = idx["by_year"].get((key_auth, int(year)), [])
        lo, hi = int(fine) * (1 - FINE_TOLERANCE), int(fine) * (1 + FINE_TOLERANCE)
        matched = [s for f, s in candidates if lo <= f <= hi]
        unique  = list(set(matched))
        if len(unique) == 1:
            return unique[0], "fuzzy"

    return None, "miss"


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill sector from Enforcement Tracker")
    parser.add_argument("--dry-run", action="store_true", help="Show matches without writing to DB")
    parser.add_argument("--limit",   type=int, default=None, help="Max docs to process")
    parser.add_argument("--cache",   action="store_true",    help="Cache/reuse tracker download")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL not set")

    records = fetch_tracker(use_cache=args.cache)
    if not records:
        sys.exit("ERROR: no Enforcement Tracker records fetched")

    idx = build_tracker_index(records)
    log.info("Index: %d primary keys (auth+year+fine), %d fuzzy buckets (auth+year)",
             len(idx["by_fine"]), len(idx["by_year"]))

    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    # Query GDPRhub docs with sector = NULL
    limit_sql = f"LIMIT {args.limit}" if args.limit else ""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, authority, decision_year, fine_amount
            FROM documents
            WHERE source = 'gdprhub'
              AND sector IS NULL
            ORDER BY fine_amount DESC NULLS LAST
            {limit_sql}
        """)
        docs = cur.fetchall()

    log.info("GDPRhub docs with sector=NULL: %d", len(docs))

    counts: dict[str, int] = {"primary": 0, "fuzzy": 0, "miss": 0}

    with conn.cursor() as cur:
        for doc_id, auth, year, fine in docs:
            sector, tier = lookup_sector(idx, auth or "", year, fine)

            counts[tier] = counts.get(tier, 0) + 1

            if sector is None:
                continue

            if args.dry_run:
                log.info("  [%s] %s | %s | fine=%s → %s",
                         tier.upper(), str(doc_id)[:8], auth, fine, sector)
            else:
                cur.execute(
                    "UPDATE documents SET sector = %s WHERE id = %s",
                    (sector, doc_id),
                )

    conn.close()

    total_matched = counts["primary"] + counts.get("fuzzy", 0)
    action = "(dry-run)" if args.dry_run else "updated in DB"
    log.info(
        "Done. %d/%d docs matched %s — primary=%d, fuzzy=%d, miss=%d",
        total_matched, len(docs), action,
        counts["primary"], counts.get("fuzzy", 0), counts["miss"],
    )
    if total_matched and not args.dry_run:
        log.info("Next step: python db/build_corpus_index.py  (to refresh sector in search index)")


if __name__ == "__main__":
    main()
