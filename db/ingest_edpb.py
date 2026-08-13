"""
JurisMind — Ingesta EDPB One-Stop-Shop Register

Fuente: https://edpb.europa.eu/our-work-tools/consistency-findings/register-for-article-60-final-decisions_en
  1,341 decisiones cross-border (Art. 60 GDPR).
  Metadatos estructurados en HTML + texto de PDFs cuando es extraible.

Destino: tabla documents (source='edpb_oss').

Uso:
    PYTHONUTF8=1 python db/ingest_edpb.py              # todas las paginas
    PYTHONUTF8=1 python db/ingest_edpb.py --limit 20    # primeros 20 casos
    PYTHONUTF8=1 python db/ingest_edpb.py --no-pdf      # solo metadatos, sin descargar PDFs
"""

import argparse
import io
import logging
import os
import re
import sys
import time

import fitz  # PyMuPDF
import psycopg
import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

# -- Config -------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BASE_URL = "https://edpb.europa.eu"
REGISTER_URL = f"{BASE_URL}/our-work-tools/consistency-findings/register-for-article-60-final-decisions_en"
BATCH_SIZE = 10
REQUEST_DELAY = 1.5
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# -- Country code mapping -----------------------------------------------------

COUNTRY_CODES = {
    "at": "Austria", "be": "Belgium", "bg": "Bulgaria", "cy": "Cyprus",
    "cz": "Czech Republic", "de": "Germany", "dk": "Denmark", "ee": "Estonia",
    "es": "Spain", "fi": "Finland", "fr": "France", "gr": "Greece",
    "hr": "Croatia", "hu": "Hungary", "ie": "Ireland", "is": "Iceland",
    "it": "Italy", "li": "Liechtenstein", "lt": "Lithuania", "lu": "Luxembourg",
    "lv": "Latvia", "mt": "Malta", "nl": "Netherlands", "no": "Norway",
    "pl": "Poland", "pt": "Portugal", "ro": "Romania", "se": "Sweden",
    "si": "Slovenia", "sk": "Slovakia",
}


# -- PDF text extraction ------------------------------------------------------

def extract_pdf_text(pdf_url: str) -> str | None:
    """Download PDF and extract text. Returns None if image-based or error."""
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("PDF download failed %s: %s", pdf_url, e)
        return None

    try:
        doc = fitz.open(stream=r.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as e:
        log.warning("PDF parse failed %s: %s", pdf_url, e)
        return None

    # If less than 100 chars, it's likely image-based
    return text.strip() if len(text.strip()) > 100 else None


# -- HTML parsing -------------------------------------------------------------

def _extract_articles(legal_ref_text: str) -> list[str]:
    """Extract GDPR article numbers from legal reference text."""
    if not legal_ref_text:
        return []
    matches = re.findall(r"Article (\d+)", legal_ref_text)
    return [f"Article {m} GDPR" for m in matches]


def parse_page(html: str) -> list[dict]:
    """Parse one page of EDPB OSS register into list of dicts."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_="view-foss-decisions-overview-overview__content")
    if not content:
        return []

    rows = content.find_all("div", class_="view-foss-decisions-overview-overview__row")
    cases: list[dict] = []

    for row in rows:
        try:
            # Case ID
            id_div = row.find("div", class_="foss-decision-foss-decision-teaser__id")
            case_id = id_div.get_text(strip=True) if id_div else None
            if not case_id:
                continue

            # Date
            time_tag = row.find("time")
            date_str = time_tag["datetime"][:10] if time_tag and time_tag.get("datetime") else None

            # Lead SA
            lead_div = row.find("div", class_="foss-decision-foss-decision-teaser__lead-sa")
            lead_code = None
            if lead_div:
                code_div = lead_div.find("div", class_="member-country-token__code")
                if code_div:
                    lead_code = code_div.get_text(strip=True).lower()

            # PDF link
            pdf_link = row.find("a", class_="file__link")
            pdf_path = pdf_link["href"] if pdf_link else None

            # Metadata from details section
            legal_ref = row.find("dd", class_="foss-decision-teaser__main-legel-ref-value")
            legal_ref_text = legal_ref.get_text(strip=True) if legal_ref else None

            csa_dd = row.find("dd", class_="foss-decision-teaser__concerned-sa-value")
            csa_text = csa_dd.get_text(", ", strip=True) if csa_dd else None

            topics_dd = row.find("dd", class_="foss-decision-teaser__relevant-topics-value")
            topics_items = []
            if topics_dd:
                for li in topics_dd.find_all("li"):
                    topics_items.append(li.get_text(strip=True))
                if not topics_items:
                    topics_items = [topics_dd.get_text(strip=True)]

            outcome_dd = row.find("dd", class_="foss-decision-teaser__outcome-value")
            outcome_text = outcome_dd.get_text(strip=True) if outcome_dd else None

            cases.append({
                "case_id": case_id,
                "decision_date": date_str,
                "lead_sa_code": lead_code,
                "lead_sa_country": COUNTRY_CODES.get(lead_code, lead_code),
                "legal_reference": legal_ref_text,
                "gdpr_articles": _extract_articles(legal_ref_text),
                "csas": csa_text,
                "topics": topics_items,
                "outcome": outcome_text,
                "pdf_path": pdf_path,
            })
        except Exception as e:
            log.warning("Error parsing EDPB row: %s", e)
            continue

    return cases


# -- Fetching -----------------------------------------------------------------

def fetch_all_cases(limit: int = 0) -> list[dict]:
    """Fetch all cases from EDPB OSS register. limit=0 means all."""
    all_cases: list[dict] = []
    page = 0

    while True:
        url = f"{REGISTER_URL}?page={page}"
        log.info("Fetching page %d: %s", page, url)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("Page %d failed: %s — skipping", page, e)
            page += 1
            time.sleep(REQUEST_DELAY)
            if page > 130:
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

_UPSERT_DOC = """
INSERT INTO documents (
    source, source_id, document_type, pipeline_version,
    title, jurisdiction, authority, authority_abbrev,
    decision_date, decision_year,
    case_type, outcome,
    gdpr_articles, source_urls,
    summary_teaser,
    full_text, full_text_language, content_depth,
    source_metadata,
    search_vector
) VALUES (
    'edpb_oss', %(source_id)s, 'dpa_decision', '1.0.0',
    %(title)s, %(jurisdiction)s, %(authority)s, %(authority_abbrev)s,
    %(decision_date)s, %(decision_year)s,
    'Cross-border (Art. 60)', %(outcome)s,
    %(gdpr_articles)s, %(source_urls)s,
    %(summary_teaser)s,
    %(full_text)s, %(full_text_language)s, %(content_depth)s,
    %(source_metadata)s,
    to_tsvector('english',
        coalesce(%(title)s,'') || ' ' ||
        coalesce(%(summary_teaser)s,'') || ' ' ||
        coalesce(%(full_text)s,''))
)
ON CONFLICT (source, source_id) DO UPDATE SET
    title            = EXCLUDED.title,
    outcome          = EXCLUDED.outcome,
    gdpr_articles    = EXCLUDED.gdpr_articles,
    full_text        = COALESCE(EXCLUDED.full_text, documents.full_text),
    content_depth    = EXCLUDED.content_depth,
    source_metadata  = EXCLUDED.source_metadata,
    search_vector    = EXCLUDED.search_vector,
    updated_at       = now()
"""


def upsert_cases(conn: psycopg.Connection, cases: list[dict], download_pdfs: bool = True) -> dict:
    """Upsert cases into documents. Returns stats dict."""
    stats = {"upserted": 0, "pdf_text": 0, "pdf_image": 0, "pdf_skip": 0}

    with conn.cursor() as cur:
        for i, case in enumerate(cases):
            # Extract PDF text if requested
            full_text = None
            if download_pdfs and case["pdf_path"]:
                pdf_url = f"{BASE_URL}{case['pdf_path']}"
                full_text = extract_pdf_text(pdf_url)
                if full_text:
                    stats["pdf_text"] += 1
                else:
                    stats["pdf_image"] += 1
                time.sleep(0.5)  # rate limit PDF downloads
            else:
                stats["pdf_skip"] += 1

            # Build title
            lead = case["lead_sa_country"] or case["lead_sa_code"] or "Unknown"
            title = f"EDPB OSS — {lead} — {case['case_id']}"

            # Build teaser from topics + outcome
            parts = []
            if case["topics"]:
                parts.append(", ".join(case["topics"]))
            if case["outcome"]:
                parts.append(f"Outcome: {case['outcome']}")
            teaser = ". ".join(parts) if parts else None

            year = int(case["decision_date"][:4]) if case["decision_date"] else None

            params = {
                "source_id": case["case_id"],
                "title": title,
                "jurisdiction": case["lead_sa_country"] or lead,
                "authority": f"EDPB OSS ({lead})",
                "authority_abbrev": (case["lead_sa_code"] or "").upper(),
                "decision_date": case["decision_date"],
                "decision_year": year,
                "outcome": case["outcome"],
                "gdpr_articles": case["gdpr_articles"],
                "source_urls": Jsonb([{
                    "url": f"{BASE_URL}{case['pdf_path']}",
                    "name": "EDPB PDF",
                    "language": "English",
                }]) if case["pdf_path"] else None,
                "summary_teaser": teaser,
                "full_text": full_text,
                "full_text_language": "en" if full_text else None,
                "content_depth": "full" if full_text else "metadata",
                "source_metadata": Jsonb({
                    "edpb_id": case["case_id"],
                    "lead_sa": case["lead_sa_code"],
                    "csas": case["csas"],
                    "topics": case["topics"],
                    "legal_reference": case["legal_reference"],
                }),
            }

            try:
                cur.execute(_UPSERT_DOC, params)
                stats["upserted"] += 1
            except Exception as e:
                log.warning("Case %s: upsert error — %s", case["case_id"], e)
                conn.rollback()
                continue

            if stats["upserted"] % BATCH_SIZE == 0:
                conn.commit()
                log.info("Upserted %d/%d (text: %d, image: %d)...",
                         stats["upserted"], len(cases), stats["pdf_text"], stats["pdf_image"])

    conn.commit()
    return stats


# -- Main ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest EDPB OSS Register into documents")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N cases (0 = all)")
    parser.add_argument("--no-pdf", action="store_true",
                        help="Skip PDF download, only store metadata")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        sys.exit(1)

    # Fetch cases from EDPB
    cases = fetch_all_cases(limit=args.limit)
    if not cases:
        log.warning("No se obtuvieron casos del EDPB")
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
        stats = upsert_cases(conn, cases, download_pdfs=not args.no_pdf)

    conn.close()
    log.info("Ingesta EDPB completa: %d upserted, %d con texto, %d image-only, %d sin PDF",
             stats["upserted"], stats["pdf_text"], stats["pdf_image"], stats["pdf_skip"])


if __name__ == "__main__":
    main()
