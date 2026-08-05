"""
JurisMind — Pipeline de ingesta real

Fuentes:
  1. GDPR Enforcement Tracker  — 3.202 casos (HTML scraping → JSON embebido)
  2. GDPRhub (MediaWiki API)   — 4.500+ decisiones DPA + tribunales
  3. EUR-Lex CELLAR (SPARQL)   — sentencias TJUE que citan el GDPR

Uso:
    DATABASE_URL=postgresql://... python db/ingest.py [--source all|tracker|gdprhub|eurlex]

Notas:
  - Idempotente: ON CONFLICT DO UPDATE — seguro de ejecutar varias veces
  - Los embeddings NO se generan aquí; ese es un paso separado (embed.py)
  - pipeline_version en documents permite re-ingestar selectivamente al actualizar el parser
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg
import requests
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).parent))
from chunker import make_chunks

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL       = os.environ.get("DATABASE_URL", "")
PIPELINE_VERSION   = "1.0.0"
TRACKER_URL        = "https://www.enforcementtracker.com/"
GDPRHUB_API        = "https://gdprhub.eu/api.php"
SPARQL_ENDPOINT    = "https://publications.europa.eu/webapi/rdf/sparql"
GDPR_CELLAR_URI    = "http://publications.europa.eu/resource/cellar/3e485e15-11bd-11e6-ba9a-01aa75ed71a1"
BATCH_SIZE         = 10    # documentos por commit (50 era demasiado para CockroachDB Serverless lock budget)
REQUEST_DELAY      = 0.35  # segundos entre requests HTTP

HEADERS = {"User-Agent": "JurisMind/1.0 (research@jurismind.dev)"}
HEADERS_SPARQL = {**HEADERS, "Accept": "application/sparql-results+json"}

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Normalización — Enforcement Tracker ────────────────────────────────────────

def _parse_date(date_str: str) -> tuple[str | None, int | None]:
    """Devuelve (ISO date o None, year o None)."""
    if not date_str:
        return None, None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str, int(date_str[:4])
    if re.match(r"^\d{4}$", date_str):
        return None, int(date_str)
    return None, None


def normalize_tracker(raw: dict) -> dict:
    decision_date, decision_year = _parse_date(raw.get("d", ""))
    if not decision_year and raw.get("y"):
        decision_year = int(raw["y"])

    articles_raw = raw.get("r", "")
    gdpr_articles = [a.strip() for a in articles_raw.split(",") if a.strip()] if articles_raw else []

    fine = raw.get("f")
    fine_amount = int(fine) if fine and fine > 0 else None

    authority = raw.get("a", "")
    country   = raw.get("C", "")
    subject   = raw.get("p", "") or "Unknown entity"

    return {
        "source":           "enforcement_tracker",
        "source_id":        str(raw["e"]),
        "document_type":    "dpa_decision",
        "title":            f"{authority} — {subject} ({country})",
        "jurisdiction":     country or None,
        "authority":        authority or None,
        "authority_abbrev": None,
        "case_number":      None,
        "ecli":             None,
        "celex":            None,
        "decision_date":    decision_date,
        "decision_year":    decision_year,
        "date_published":   None,
        "date_started":     None,
        "case_type":        None,
        "outcome":          None,
        "fine_amount":      fine_amount,
        "fine_currency":    "EUR" if fine_amount else None,
        "sector":           raw.get("s") or None,
        "controller_name":  subject if subject != "Unknown" else None,
        "gdpr_articles":    gdpr_articles,
        "parties":          None,
        "source_urls":      [{"url": raw["u"], "language": None, "name": "Official source"}] if raw.get("u") else None,
        "national_laws":    None,
        "eu_laws":          None,
        "appeal_chain":     None,
        "summary_teaser":   raw.get("t") or None,
        "summary_facts":    None,
        "summary_dispute":  None,
        "summary_holding":  None,
        "full_text":        None,
        "full_text_language": None,
    }


# ── Normalización — GDPRhub ────────────────────────────────────────────────────

def _parse_ddmmyyyy(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def _clean_wikitext(value: str) -> str:
    value = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', value)
    value = re.sub(r'\{\{[^}]+\}\}', '', value)
    return value.strip()


def parse_template_fields(wikitext: str) -> dict:
    """Parseo línea a línea — corrige el bug del regex DOTALL."""
    fields: dict[str, str] = {}
    in_template = False
    for line in wikitext.split('\n'):
        s = line.strip()
        if not in_template:
            if s.startswith('{{'):
                in_template = True
            continue
        if s.startswith('}}'):
            break
        if not s.startswith('|'):
            continue
        rest = s[1:]
        if '=' not in rest:
            continue
        key, _, value = rest.partition('=')
        key   = key.strip()
        value = _clean_wikitext(value)
        if key and value:
            fields[key] = value
    return fields


def extract_english_summary(wikitext: str) -> dict:
    end = wikitext.find('}}')
    if end == -1:
        return {}
    body  = wikitext[end + 2:].strip()
    lines = body.split('\n')
    teaser = next((l.strip() for l in lines if l.strip() and not l.strip().startswith('=')), '')
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        s = line.strip()
        if s.startswith('==='):
            current = s.strip('= ').lower()
            sections[current] = []
        elif s.startswith('=='):
            continue
        elif current is not None:
            sections[current].append(line)
    return {
        'teaser':  teaser,
        'facts':   '\n'.join(sections.get('facts',   [])).strip(),
        'dispute': '\n'.join(sections.get('dispute', [])).strip(),
        'holding': '\n'.join(sections.get('holding', [])).strip(),
    }


def normalize_gdprhub(title: str, fields: dict, summary: dict) -> dict:
    is_court    = bool(fields.get("Court_Abbrevation") or fields.get("Court_With_Country"))
    doc_type    = "court_judgment" if is_court else "dpa_decision"
    authority   = fields.get("DPA_With_Country") or fields.get("Court_With_Country")
    abbrev      = fields.get("DPA_Abbrevation")  or fields.get("Court_Abbrevation")

    year_str    = fields.get("Year", "")
    dec_year    = int(year_str) if year_str and year_str.isdigit() else None
    dec_date    = _parse_ddmmyyyy(fields.get("Date_Decided"))
    if not dec_year and dec_date:
        dec_year = int(dec_date[:4])

    fine_str    = fields.get("Fine", "")
    try:
        fine_amount = int(float(fine_str)) if fine_str and fine_str.strip() else None
    except (ValueError, TypeError):
        fine_amount = None

    gdpr_articles = [fields[f"GDPR_Article_{i}"] for i in range(1, 10) if fields.get(f"GDPR_Article_{i}")]
    parties       = [{"name": fields[f"Party_Name_{i}"], "url": fields.get(f"Party_Link_{i}")}
                     for i in range(1, 6) if fields.get(f"Party_Name_{i}")]
    source_urls   = [{"name": fields.get(f"Original_Source_Name_{i}"),
                      "url": fields[f"Original_Source_Link_{i}"],
                      "language": fields.get(f"Original_Source_Language_{i}"),
                      "language_code": fields.get(f"Original_Source_Language__Code_{i}")}
                     for i in range(1, 4) if fields.get(f"Original_Source_Link_{i}")]
    national_laws = [{"name": fields[f"National_Law_Name_{i}"], "url": fields.get(f"National_Law_Link_{i}")}
                     for i in range(1, 6) if fields.get(f"National_Law_Name_{i}")]
    eu_laws       = [{"name": fields[f"EU_Law_Name_{i}"], "url": fields.get(f"EU_Law_Link_{i}")}
                     for i in range(1, 3) if fields.get(f"EU_Law_Name_{i}")]

    appeal_chain: dict = {}
    if fields.get("Appeal_From_Body"):
        appeal_chain["from"] = {
            "body":        fields["Appeal_From_Body"],
            "case_number": fields.get("Appeal_From_Case_Number_Name"),
            "status":      fields.get("Appeal_From_Status"),
            "url":         fields.get("Appeal_From_Link"),
        }
    if fields.get("Appeal_To_Body"):
        appeal_chain["to"] = {
            "body":        fields["Appeal_To_Body"],
            "case_number": fields.get("Appeal_To_Case_Number_Name"),
            "status":      fields.get("Appeal_To_Status"),
            "url":         fields.get("Appeal_To_Link"),
        }

    ecli = fields.get("ECLI") or None
    if ecli and not ecli.startswith("ECLI:"):
        ecli = None  # descartar valores malformados

    return {
        "source":           "gdprhub",
        "source_id":        title,
        "document_type":    doc_type,
        "title":            title,
        "jurisdiction":     fields.get("Jurisdiction") or None,
        "authority":        authority or None,
        "authority_abbrev": abbrev or None,
        "case_number":      fields.get("Case_Number_Name") or None,
        "ecli":             ecli,
        "celex":            None,
        "decision_date":    dec_date,
        "decision_year":    dec_year,
        "date_published":   _parse_ddmmyyyy(fields.get("Date_Published")),
        "date_started":     _parse_ddmmyyyy(fields.get("Date_Started")),
        "case_type":        fields.get("Type") or None,
        "outcome":          fields.get("Outcome") or None,
        "fine_amount":      fine_amount,
        "fine_currency":    fields.get("Currency") or ("EUR" if fine_amount else None),
        "sector":           None,
        "controller_name":  parties[0]["name"] if parties else None,
        "gdpr_articles":    gdpr_articles,
        "parties":          parties or None,
        "source_urls":      source_urls or None,
        "national_laws":    national_laws or None,
        "eu_laws":          eu_laws or None,
        "appeal_chain":     appeal_chain or None,
        "summary_teaser":   summary.get("teaser") or None,
        "summary_facts":    summary.get("facts") or None,
        "summary_dispute":  summary.get("dispute") or None,
        "summary_holding":  summary.get("holding") or None,
        "full_text":        None,
        "full_text_language": None,
    }


def normalize_eurlex(raw: dict) -> dict:
    return {
        "source":           "eurlex",
        "source_id":        raw["celex"],
        "document_type":    "cjeu_judgment",
        "title":            raw.get("celex", ""),
        "jurisdiction":     "EU",
        "authority":        "Court of Justice of the European Union",
        "authority_abbrev": "CJEU",
        "case_number":      raw.get("celex"),
        "ecli":             raw.get("ecli") or None,
        "celex":            raw.get("celex"),
        "decision_date":    raw.get("date") or None,
        "decision_year":    int(raw["date"][:4]) if raw.get("date") else None,
        "date_published":   None,
        "date_started":     None,
        "case_type":        "Preliminary ruling" if raw.get("celex", "").endswith("CJ") or "CJ" in raw.get("celex", "") else None,
        "outcome":          None,
        "fine_amount":      None,
        "fine_currency":    None,
        "sector":           None,
        "controller_name":  None,
        "gdpr_articles":    [],
        "parties":          None,
        "source_urls":      [{"url": f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{raw['celex']}",
                              "language": "English", "name": "EUR-Lex"}] if raw.get("celex") else None,
        "national_laws":    None,
        "eu_laws":          [{"name": "GDPR (Regulation 2016/679)",
                              "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj"}],
        "appeal_chain":     None,
        "summary_teaser":   None,
        "summary_facts":    None,
        "summary_dispute":  None,
        "summary_holding":  None,
        "full_text":        None,
        "full_text_language": None,
    }


# ── DB helpers ─────────────────────────────────────────────────────────────────

_UPSERT_DOC = """
INSERT INTO documents (
    source, source_id, document_type, pipeline_version,
    title, jurisdiction, authority, authority_abbrev,
    case_number, ecli, celex,
    decision_date, decision_year, date_published, date_started,
    case_type, outcome, fine_amount, fine_currency,
    sector, controller_name,
    gdpr_articles, parties, source_urls, national_laws, eu_laws, appeal_chain,
    summary_teaser, summary_facts, summary_dispute, summary_holding,
    full_text, full_text_language, search_vector
) VALUES (
    %(source)s, %(source_id)s, %(document_type)s, %(pipeline_version)s,
    %(title)s, %(jurisdiction)s, %(authority)s, %(authority_abbrev)s,
    %(case_number)s, %(ecli)s, %(celex)s,
    %(decision_date)s, %(decision_year)s, %(date_published)s, %(date_started)s,
    %(case_type)s, %(outcome)s, %(fine_amount)s, %(fine_currency)s,
    %(sector)s, %(controller_name)s,
    %(gdpr_articles)s, %(parties)s, %(source_urls)s, %(national_laws)s, %(eu_laws)s, %(appeal_chain)s,
    %(summary_teaser)s, %(summary_facts)s, %(summary_dispute)s, %(summary_holding)s,
    %(full_text)s, %(full_text_language)s,
    to_tsvector('english',
        coalesce(%(title)s,'') || ' ' ||
        coalesce(%(summary_teaser)s,'') || ' ' ||
        coalesce(%(summary_facts)s,'') || ' ' ||
        coalesce(%(summary_holding)s,''))
)
ON CONFLICT (source, source_id) DO UPDATE SET
    title            = EXCLUDED.title,
    outcome          = EXCLUDED.outcome,
    fine_amount      = EXCLUDED.fine_amount,
    gdpr_articles    = EXCLUDED.gdpr_articles,
    appeal_chain     = EXCLUDED.appeal_chain,
    summary_teaser   = EXCLUDED.summary_teaser,
    summary_facts    = EXCLUDED.summary_facts,
    summary_dispute  = EXCLUDED.summary_dispute,
    summary_holding  = EXCLUDED.summary_holding,
    search_vector    = EXCLUDED.search_vector,
    pipeline_version = EXCLUDED.pipeline_version,
    updated_at       = now()
RETURNING id
"""

_INSERT_CHUNK = """
INSERT INTO chunks (id, document_id, chunk_type, parent_id, chunk_index,
                    content, content_tokens, section, search_vector)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', %s))
"""


def _prep(doc: dict) -> dict:
    """Convierte campos JSONB a Jsonb() y añade pipeline_version."""
    d = dict(doc)
    d["pipeline_version"] = PIPELINE_VERSION
    for field in ("parties", "source_urls", "national_laws", "eu_laws", "appeal_chain"):
        if d.get(field) is not None:
            d[field] = Jsonb(d[field])
    return d


def upsert_document_and_chunks(cur: psycopg.Cursor, doc: dict) -> str:
    """Upserta documento + genera chunks solo si el doc es nuevo. Devuelve UUID."""
    cur.execute(_UPSERT_DOC, _prep(doc))
    doc_id = str(cur.fetchone()[0])

    # Solo generar chunks si el doc no tiene ninguno todavía.
    # Esto preserva embeddings ya calculados cuando se re-ejecuta ingest.
    cur.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (doc_id,))
    if cur.fetchone()[0] > 0:
        return doc_id  # ya chunkado — no tocar

    chunks = make_chunks(doc_id, doc)
    if chunks:
        cur.executemany(
            _INSERT_CHUNK,
            [(c["id"], c["document_id"], c["chunk_type"], c["parent_id"],
              c["chunk_index"], c["content"], c["content_tokens"], c["section"],
              c["content"])
             for c in chunks],
        )
    return doc_id


# ── Fuente 1: Enforcement Tracker ──────────────────────────────────────────────

def fetch_tracker_records() -> list[dict]:
    log.info("Tracker: descargando HTML de %s ...", TRACKER_URL)
    r = requests.get(TRACKER_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text
    log.info("Tracker: HTML descargado (%d bytes). Extrayendo JSON...", len(html))

    patterns = [
        r'var\s+\w*[Dd]ata\w*\s*=\s*(\[.*?\])\s*;',
        r'var\s+\w*[Cc]ases\w*\s*=\s*(\[.*?\])\s*;',
        r'=\s*(\[\s*\{"e"\s*:\s*1\b.*?\])\s*[,;]',
        r'(\[\s*\{"e"\s*:\s*1\b.*?\])',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                log.info("Tracker: %d registros encontrados.", len(data))
                return data
            except json.JSONDecodeError:
                continue

    log.error("Tracker: no se encontró el JSON embebido. Guardando HTML para inspección.")
    (Path(__file__).parent.parent / "data" / "tracker_raw.html").write_text(html, encoding="utf-8")
    return []


def ingest_enforcement_tracker(conn: psycopg.Connection) -> int:
    records = fetch_tracker_records()
    if not records:
        return 0

    count = 0
    with conn.cursor() as cur:
        for i, raw in enumerate(records):
            try:
                doc = normalize_tracker(raw)
                upsert_document_and_chunks(cur, doc)
                count += 1
            except Exception as e:
                log.warning("Tracker ID=%s: error — %s", raw.get("e"), e)
                continue

            if count % BATCH_SIZE == 0:
                conn.commit()
                log.info("Tracker: %d/%d insertados...", count, len(records))

    conn.commit()
    log.info("Tracker: ingesta completa — %d documentos.", count)
    return count


# ── Fuente 2: GDPRhub ──────────────────────────────────────────────────────────

def _gdprhub_get(params: dict, timeout: int = 60) -> dict:
    r = requests.get(GDPRHUB_API, params={**params, "format": "json"}, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _is_decision(title: str, fields: dict) -> bool:
    has_dpa   = bool(fields.get("DPA_Abbrevation") or fields.get("DPA_With_Country"))
    has_court = bool(fields.get("Court_Abbrevation") or fields.get("Court_With_Country"))
    has_case  = bool(fields.get("Case_Number_Name"))
    is_meta   = title.startswith(("Article", "Recital", "Data Protection in", "Category:"))
    return (has_dpa or has_court or has_case) and not is_meta


def _gdprhub_all_titles() -> list[str]:
    """
    Obtiene todos los títulos de decisiones via allpages (namespace 0) con paginación.
    Filtra por patrón de título: "Authority (Country) - CaseNumber" o "Court - CaseNumber".
    """
    titles: list[str] = []
    params: dict = {
        "action":    "query",
        "list":      "allpages",
        "aplimit":   "500",
        "apnamespace": "0",
    }
    log.info("GDPRhub: enumerando todas las páginas...")
    while True:
        data     = _gdprhub_get(params)
        pages    = data.get("query", {}).get("allpages", [])
        for page in pages:
            title = page["title"]
            # Las decisiones tienen un " - " entre la autoridad y el número de caso
            if " - " in title and not title.startswith(("Article", "Recital", "GDPR", "Category")):
                titles.append(title)
        cont = data.get("continue", {}).get("apcontinue")
        if not cont:
            break
        params["apfrom"] = cont
        time.sleep(REQUEST_DELAY)

    log.info("GDPRhub: %d títulos candidatos.", len(titles))
    return titles


def ingest_gdprhub(conn: psycopg.Connection, limit: int = 0) -> int:
    titles = _gdprhub_all_titles()
    if limit:
        titles = titles[:limit]

    count = 0
    with conn.cursor() as cur:
        for title in titles:
            # Fetch wikitext
            try:
                data = _gdprhub_get({
                    "action": "parse",
                    "page":   title,
                    "prop":   "wikitext",
                })
                if "error" in data:
                    continue
                wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
                if not wikitext:
                    continue
            except Exception as e:
                log.warning("GDPRhub '%s': fetch error — %s", title, e)
                time.sleep(REQUEST_DELAY)
                continue

            fields  = parse_template_fields(wikitext)
            summary = extract_english_summary(wikitext)

            if not _is_decision(title, fields):
                time.sleep(REQUEST_DELAY)
                continue

            try:
                doc = normalize_gdprhub(title, fields, summary)
                upsert_document_and_chunks(cur, doc)
                count += 1
            except Exception as e:
                log.warning("GDPRhub '%s': normalización error — %s", title, e)
                conn.rollback()  # reset aborted transaction before next doc
                time.sleep(REQUEST_DELAY)
                continue

            if count % BATCH_SIZE == 0:
                conn.commit()
                log.info("GDPRhub: %d documentos insertados...", count)

            time.sleep(REQUEST_DELAY)

    conn.commit()
    log.info("GDPRhub: ingesta completa — %d documentos.", count)
    return count


# ── Fuente 3: EUR-Lex CELLAR ───────────────────────────────────────────────────

_SPARQL_CJEU_GDPR = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?work ?celex ?date ?ecli WHERE {{
  ?work cdm:work_cites_work <{GDPR_CELLAR_URI}> .
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(REGEX(STR(?celex), "^6[0-9]{{4}}CJ"))
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli }}
}}
ORDER BY DESC(?date)
LIMIT 500
"""


def ingest_eurlex(conn: psycopg.Connection) -> int:
    log.info("EUR-Lex: ejecutando SPARQL...")
    try:
        r = requests.post(
            SPARQL_ENDPOINT,
            data={"query": _SPARQL_CJEU_GDPR},
            headers=HEADERS_SPARQL,
            timeout=30,
        )
        r.raise_for_status()
        bindings = r.json().get("results", {}).get("bindings", [])
    except Exception as e:
        log.error("EUR-Lex SPARQL error: %s", e)
        return 0

    log.info("EUR-Lex: %d sentencias encontradas.", len(bindings))
    count = 0
    with conn.cursor() as cur:
        for b in bindings:
            raw = {
                "celex": b.get("celex", {}).get("value", ""),
                "date":  b.get("date",  {}).get("value", ""),
                "ecli":  b.get("ecli",  {}).get("value", ""),
                "work":  b.get("work",  {}).get("value", ""),
            }
            if not raw["celex"]:
                continue
            try:
                doc = normalize_eurlex(raw)
                upsert_document_and_chunks(cur, doc)
                count += 1
            except Exception as e:
                log.warning("EUR-Lex CELEX=%s: error — %s", raw.get("celex"), e)

            if count % BATCH_SIZE == 0:
                conn.commit()
                log.info("EUR-Lex: %d insertados...", count)

    conn.commit()
    log.info("EUR-Lex: ingesta completa — %d documentos.", count)
    return count


# ── Orquestador ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind ingestion pipeline")
    parser.add_argument("--source", choices=["all", "tracker", "gdprhub", "eurlex"],
                        default="all", help="Fuente a ingestar (default: all)")
    parser.add_argument("--gdprhub-limit", type=int, default=0,
                        help="Limitar GDPRhub a N documentos (0 = sin límite)")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado. Ejemplo:")
        log.error("  export DATABASE_URL='postgresql://user:pass@host:26257/jurismind?sslmode=verify-full'")
        sys.exit(1)

    log.info("Conectando a CockroachDB...")
    try:
        conn = psycopg.connect(DATABASE_URL)
    except Exception as e:
        log.error("No se pudo conectar: %s", e)
        sys.exit(1)

    totals: dict[str, int] = {}

    with conn:
        if args.source in ("all", "tracker"):
            totals["tracker"] = ingest_enforcement_tracker(conn)

        if args.source in ("all", "gdprhub"):
            totals["gdprhub"] = ingest_gdprhub(conn, limit=args.gdprhub_limit)

        if args.source in ("all", "eurlex"):
            totals["eurlex"] = ingest_eurlex(conn)

    conn.close()

    log.info("=" * 50)
    log.info("Ingesta completada:")
    for source, n in totals.items():
        log.info("  %-20s %d documentos", source, n)
    log.info("Siguiente paso: python db/embed.py  (genera embeddings)")


if __name__ == "__main__":
    main()
