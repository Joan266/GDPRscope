"""
Probe AEPD (Agencia Espanola de Proteccion de Datos) for sanctioning resolution PDFs.

URL pattern: https://www.aepd.es/documento/ps-XXXXX-YYYY.pdf
Where XXXXX = zero-padded 5-digit case number, YYYY = year.

Uses HEAD requests only. Respectful 0.3s delay between requests.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Configuration ---
BASE_URL = "https://www.aepd.es/documento/ps-{number:05d}-{year}.pdf"
YEARS = [2026, 2025, 2024, 2023, 2022]
MAX_NUMBER = 600
DELAY_NORMAL = 0.3
DELAY_BACKOFF = 1.0
PROGRESS_INTERVAL = 50
FLUSH_INTERVAL = 50

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "aepd_ps_urls.json"


def build_url(year: int, number: int) -> str:
    return BASE_URL.format(number=number, year=year)


def probe_url(session: requests.Session, url: str, delay: float) -> dict[str, Any] | None:
    """Send HEAD request. Returns dict with status info, or None on unrecoverable error."""
    for attempt in range(2):  # retry once on failure
        try:
            resp = session.head(url, timeout=15, allow_redirects=True)
            time.sleep(delay)
            return {
                "status": resp.status_code,
                "content_length": resp.headers.get("Content-Length"),
                "content_type": resp.headers.get("Content-Type"),
            }
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == 0:
                log.warning("Connection error on %s, retrying in 2s: %s", url, e)
                time.sleep(2)
            else:
                log.error("Failed after retry: %s — skipping", url)
                return None
    return None


def flush_results(results: list[dict[str, Any]], path: Path) -> None:
    """Write results to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def main() -> None:
    log.info("AEPD PS Resolution Probe — starting")
    log.info("Years: %s | Numbers: 1-%d | Delay: %.1fs", YEARS, MAX_NUMBER, DELAY_NORMAL)
    log.info("Output: %s", OUTPUT_FILE)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    found: list[dict[str, Any]] = []
    # Load existing results if file exists (resume support)
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, list):
                found = existing
                log.info("Loaded %d existing results from previous run", len(found))
        except (json.JSONDecodeError, IOError):
            log.warning("Could not load existing results, starting fresh")

    # Track already-probed URLs to avoid re-probing on resume
    already_probed = {item["url"] for item in found}

    total_tested = 0
    total_skipped = 0
    by_year: dict[int, int] = {y: 0 for y in YEARS}
    delay = DELAY_NORMAL
    found_since_flush = 0

    for year in YEARS:
        log.info("=== Probing year %d (ps-00001-%d to ps-%05d-%d) ===", year, year, MAX_NUMBER, year)
        year_found = 0

        for number in range(1, MAX_NUMBER + 1):
            url = build_url(year, number)

            # Skip if already probed in a previous run
            if url in already_probed:
                total_skipped += 1
                continue

            result = probe_url(session, url, delay)
            total_tested += 1

            if result is None:
                continue

            status = result["status"]

            # Rate limiting detection
            if status == 429:
                log.warning("Rate limited (429)! Backing off to %.1fs delay", DELAY_BACKOFF)
                delay = DELAY_BACKOFF
                time.sleep(5)
                # Retry this URL
                result = probe_url(session, url, delay)
                if result:
                    status = result["status"]

            if status == 200:
                size_bytes = int(result["content_length"]) if result["content_length"] else 0
                entry = {
                    "url": url,
                    "year": year,
                    "number": number,
                    "size_bytes": size_bytes,
                }
                found.append(entry)
                year_found += 1
                found_since_flush += 1
                log.info(
                    "FOUND: ps-%05d-%d  size=%s bytes",
                    number, year,
                    f"{size_bytes:,}" if size_bytes else "unknown",
                )

            # Progress reporting
            if total_tested % PROGRESS_INTERVAL == 0:
                log.info(
                    "Progress: %d tested | %d found total | current: ps-%05d-%d | delay=%.1fs",
                    total_tested, len(found), number, year, delay,
                )

            # Incremental flush
            if found_since_flush >= FLUSH_INTERVAL:
                flush_results(found, OUTPUT_FILE)
                found_since_flush = 0
                log.info("Flushed %d results to disk", len(found))

        by_year[year] = year_found
        log.info("Year %d complete: %d found", year, year_found)

    # Final flush
    flush_results(found, OUTPUT_FILE)

    # Summary
    log.info("=" * 60)
    log.info("PROBE COMPLETE")
    log.info("Total URLs tested: %d", total_tested)
    if total_skipped:
        log.info("Skipped (already probed): %d", total_skipped)
    log.info("Total found (status 200): %d", len(found))
    log.info("-" * 40)
    for year in YEARS:
        count = by_year[year]
        log.info("  %d: %d resolutions found", year, count)
    log.info("-" * 40)
    log.info("Results saved to: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
