"""
JurisMind — Ingesta de quejas noyb.eu

Fuente: noyb Case Tracker (https://noyb.eu/en/project/cases)
  ~889 quejas estrategicas GDPR. Tabla de listado paginada (45 paginas, 20/pagina).

Destino: tabla noyb_complaints (quejas != decisiones, tabla separada de documents).

Uso:
    PYTHONUTF8=1 python db/ingest_noyb.py              # todas las paginas
    PYTHONUTF8=1 python db/ingest_noyb.py --limit 5     # primeros 5 casos
"""

import argparse
import logging
import os
import re
import sys
import time

import psycopg
import requests
from bs4 import BeautifulSoup

# -- Config -------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BASE_URL = "https://noyb.eu"
CASES_URL = f"{BASE_URL}/en/project/cases"
BATCH_SIZE = 20
REQUEST_DELAY = 1.5  # seconds between page requests
HEADERS = {"User-Agent": "JurisMind/1.0 (research@jurismind.dev)"}

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# -- Parsing ------------------------------------------------------------------

def _extract_country(dpa_text: str) -> str | None:
    """Extract country from DPA name like 'DPC (Ireland)' -> 'Ireland'."""
    m = re.search(r"\(([^)]+)\)", dpa_text)
    return m.group(1) if m else None


def parse_page(html: str) -> list[dict]:
    """Parse one page of the noyb cases table into a list of dicts."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    rows = table.find("tbody")
    if not rows:
        return []

    cases: list[dict] = []
    for tr in rows.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue

        try:
            # Column 0: Case ID + detail link
            case_link = cells[0].find("a")
            case_id = case_link.get_text(strip=True) if case_link else cells[0].get_text(strip=True)
            href = case_link["href"] if case_link and case_link.get("href") else None
            detail_url = f"{BASE_URL}{href}" if href and href.startswith("/") else href

            # Column 1: Controller
            controller = cells[1].get_text(strip=True) or None

            # Column 2: DPA(s) — may have multiple links
            dpa_links = cells[2].find_all("a")
            if dpa_links:
                dpa_names = [a.get_text(strip=True) for a in dpa_links]
            else:
                raw_dpa = cells[2].get_text(strip=True)
                dpa_names = [raw_dpa] if raw_dpa else []
            dpa_name = ", ".join(dpa_names) if dpa_names else None
            dpa_country = _extract_country(dpa_names[0]) if dpa_names else None

            # Column 3: Status
            status = cells[3].get_text(strip=True) or "Unknown"

            # Column 4: Duration — contains Filed/Closed dates + duration text
            duration_text = cells[4].get_text(" ", strip=True) or None

            cases.append({
                "case_id": case_id,
                "controller": controller,
                "dpa_name": dpa_name,
                "dpa_country": dpa_country,
                "status": status,
                "duration_text": duration_text,
                "detail_url": detail_url,
            })
        except Exception as e:
            log.warning("Error parsing row: %s", e)
            continue

    return cases


# -- Fetching -----------------------------------------------------------------

def fetch_all_cases(limit: int = 0) -> list[dict]:
    """Fetch all cases from noyb paginated table. limit=0 means all."""
    all_cases: list[dict] = []
    page = 0

    while True:
        url = f"{CASES_URL}?page=%2C{page}"
        log.info("Fetching page %d: %s", page, url)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("Page %d failed: %s — skipping", page, e)
            page += 1
            time.sleep(REQUEST_DELAY)
            # Stop after 3 consecutive empty pages to avoid infinite loop
            if page > 50:
                break
            continue

        cases = parse_page(resp.text)
        if not cases:
            log.info("Page %d returned no cases — end of pagination.", page)
            break

        all_cases.extend(cases)
        log.info("Page %d: %d cases (total: %d)", page, len(cases), len(all_cases))

        if 0 < limit <= len(all_cases):
            all_cases = all_cases[:limit]
            log.info("Limit reached: %d cases", limit)
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_cases


# -- DB upsert ----------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO noyb_complaints
    (case_id, controller, dpa_name, dpa_country, status, duration_text, detail_url)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (case_id) DO UPDATE SET
    controller = EXCLUDED.controller,
    dpa_name = EXCLUDED.dpa_name,
    dpa_country = EXCLUDED.dpa_country,
    status = EXCLUDED.status,
    duration_text = EXCLUDED.duration_text,
    detail_url = EXCLUDED.detail_url,
    updated_at = now()
"""


def upsert_cases(conn: psycopg.Connection, cases: list[dict]) -> int:
    """Batch upsert cases into noyb_complaints. Returns count of upserted rows."""
    count = 0
    with conn.cursor() as cur:
        for case in cases:
            try:
                cur.execute(UPSERT_SQL, (
                    case["case_id"],
                    case["controller"],
                    case["dpa_name"],
                    case["dpa_country"],
                    case["status"],
                    case["duration_text"],
                    case["detail_url"],
                ))
                count += 1
            except Exception as e:
                log.warning("Case %s: upsert error — %s", case.get("case_id"), e)
                conn.rollback()
                continue

            if count % BATCH_SIZE == 0:
                conn.commit()
                log.info("Upserted %d/%d...", count, len(cases))

    conn.commit()
    return count


# -- Main ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest noyb.eu case tracker into noyb_complaints")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N cases (0 = all)")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado. Ejemplo:")
        log.error("  export DATABASE_URL='postgresql://user:pass@host:5432/jurismind'")
        sys.exit(1)

    # Fetch cases from noyb.eu
    cases = fetch_all_cases(limit=args.limit)
    if not cases:
        log.warning("No se obtuvieron casos de noyb.eu")
        sys.exit(0)

    log.info("Total casos obtenidos: %d", len(cases))

    # Connect and upsert
    log.info("Conectando a DB...")
    try:
        conn = psycopg.connect(DATABASE_URL)
    except Exception as e:
        log.error("No se pudo conectar: %s", e)
        sys.exit(1)

    with conn:
        count = upsert_cases(conn, cases)

    conn.close()
    log.info("Ingesta noyb completa: %d casos upserted.", count)


if __name__ == "__main__":
    main()
