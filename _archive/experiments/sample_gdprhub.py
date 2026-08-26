"""
Sample downloader — GDPRhub (MediaWiki API)

Descarga una muestra de decisiones DPA desde gdprhub.eu usando la API pública
de MediaWiki y guarda los resultados en data/samples/gdprhub_fixed.json

Flujo:
  1. Search API → lista de títulos de decisiones
  2. Parse API → wikitext completo de cada decisión
  3. Extraer campos del template DPAdecisionBOX (parseo línea a línea)
  4. Extraer English Summary (Facts / Dispute / Holding)
  5. Guardar muestra estructurada + raw wikitext para analizar
"""

import json
import re
import time
from pathlib import Path

import requests

BASE_URL = "https://gdprhub.eu/api.php"
SAMPLE_SIZE = 20
OUT_DIR = Path(__file__).parent.parent / "data" / "samples"
OUT_FILE = OUT_DIR / "gdprhub_fixed.json"
OUT_RAW = OUT_DIR / "gdprhub_raw_wikitext.json"

HEADERS = {"User-Agent": "JurisMind-research/0.1 (data sample; contact: research@jurismind.dev)"}

SEARCH_QUERIES = [
    "DPA decision fine",
    "GDPR enforcement",
    "data protection authority decision",
]


def search_decisions(query: str, limit: int = 10) -> list[dict]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srnamespace": 0,
        "format": "json",
    }
    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("query", {}).get("search", [])


def fetch_page_wikitext(title: str) -> str | None:
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
    }
    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        return None
    return data.get("parse", {}).get("wikitext", {}).get("*", "")


def _clean_wikitext(value: str) -> str:
    """Elimina wikilinks [[A|B]]→B y templates {{...}}."""
    value = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', value)
    value = re.sub(r'\{\{[^}]+\}\}', '', value)
    return value.strip()


def parse_template_fields(wikitext: str) -> dict:
    """Parseo línea a línea del template DPAdecisionBOX / COURTdecisionBOX.

    El bug de la versión anterior (re.DOTALL) hacía que campos vacíos tragaran
    la línea siguiente. Esta versión trata cada línea como una unidad atómica.
    """
    fields: dict[str, str] = {}
    in_template = False
    for line in wikitext.split('\n'):
        stripped = line.strip()
        if not in_template:
            if stripped.startswith('{{'):
                in_template = True
            continue
        if stripped.startswith('}}'):
            break
        if not stripped.startswith('|'):
            continue
        rest = stripped[1:]
        if '=' not in rest:
            continue
        key, _, value = rest.partition('=')
        key = key.strip()
        value = _clean_wikitext(value)
        if key and value:
            fields[key] = value
    return fields


def extract_english_summary(wikitext: str) -> dict:
    """Extrae teaser + secciones Facts/Dispute/Holding del English Summary."""
    template_end = wikitext.find('}}')
    if template_end == -1:
        return {}
    body = wikitext[template_end + 2:].strip()
    lines = body.split('\n')

    teaser = next(
        (l.strip() for l in lines if l.strip() and not l.strip().startswith('=')),
        '',
    )

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        s = line.strip()
        if s.startswith('==='):
            current = s.strip('= ').lower()
            sections[current] = []
        elif s.startswith('=='):
            # nivel 2 headers (== English Summary ==) — no capturar como sección
            continue
        elif current is not None:
            sections[current].append(line)

    return {
        'teaser':  teaser,
        'facts':   '\n'.join(sections.get('facts',   [])).strip(),
        'dispute': '\n'.join(sections.get('dispute', [])).strip(),
        'holding': '\n'.join(sections.get('holding', [])).strip(),
    }


def is_decision(title: str, fields: dict) -> bool:
    """Filtra páginas que son decisiones DPA/Court reales."""
    has_dpa = "DPA_Abbrevation" in fields or "DPA_With_Country" in fields
    has_court = "Court_Abbrevation" in fields or "Court_With_Country" in fields
    has_case = "Case_Number_Name" in fields
    is_article = title.startswith("Article") or title.startswith("Recital")
    return (has_dpa or has_court or has_case) and not is_article


def print_schema(records: list[dict]) -> None:
    n = len(records)
    print("\n--- CAMPOS OBSERVADOS EN TEMPLATE ---")
    counts: dict[str, int] = {}
    for r in records:
        for k in r.get("fields", {}).keys():
            counts[k] = counts.get(k, 0) + 1
    for k, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = int(count / n * 100)
        sample = next((r["fields"][k] for r in records if k in r.get("fields", {})), "")
        print(f"  {k:<35s}  {pct:3d}%  ejemplo: {str(sample)[:50]!r}")

    print("\n--- COBERTURA DE ENGLISH SUMMARY ---")
    for section in ('teaser', 'facts', 'dispute', 'holding'):
        filled = sum(1 for r in records if r.get("summary", {}).get(section))
        print(f"  {section:<10s}  {filled}/{n}  ({int(filled/n*100)}%)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Recoger títulos únicos
    titles_seen: set[str] = set()
    titles: list[str] = []

    print("Buscando decisiones DPA en GDPRhub...")
    for query in SEARCH_QUERIES:
        results = search_decisions(query, limit=20)
        for r in results:
            t = r["title"]
            if t not in titles_seen:
                titles_seen.add(t)
                titles.append(t)
        time.sleep(0.5)

    print(f"Títulos únicos encontrados: {len(titles)}")

    # 2. Descargar wikitext, parsear campos + summary
    records: list[dict] = []
    raw_pages: list[dict] = []

    print(f"Descargando wikitext de hasta {SAMPLE_SIZE} páginas...")
    for title in titles:
        if len(records) >= SAMPLE_SIZE:
            break

        wikitext = fetch_page_wikitext(title)
        if not wikitext:
            continue

        fields = parse_template_fields(wikitext)

        if not is_decision(title, fields):
            print(f"  [skip] {title} — no es decisión DPA/Court")
            continue

        summary = extract_english_summary(wikitext)
        records.append({"title": title, "source": "gdprhub", "fields": fields, "summary": summary})
        raw_pages.append({"title": title, "wikitext": wikitext[:2000]})
        print(f"  [ok]   {title}  (facts: {'yes' if summary.get('facts') else 'no'})")
        time.sleep(0.3)

    print(f"\nDecisiones descargadas: {len(records)}")

    # 3. Guardar
    OUT_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_RAW.write_text(json.dumps(raw_pages, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Muestra guardada en: {OUT_FILE}")
    print(f"Wikitext raw en:     {OUT_RAW}")

    # 4. Mostrar schema y cobertura
    print_schema(records)

    print("\n--- PRIMER REGISTRO COMPLETO ---")
    if records:
        print(json.dumps(records[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
