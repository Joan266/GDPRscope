"""
Sample downloader — GDPR Enforcement Tracker

Descarga los datos embebidos en el HTML de enforcementtracker.com
y guarda un sample en data/samples/enforcement_tracker_sample.json

La web embebe los 3.202 casos como JSON en un bloque <script>.
Este script lo extrae, guarda los primeros N registros y muestra
la estructura de campos para informar el diseño del schema.
"""

import json
import re
import sys
from pathlib import Path

import requests

SAMPLE_SIZE = 50
OUT_DIR = Path(__file__).parent.parent / "data" / "samples"
OUT_FILE = OUT_DIR / "enforcement_tracker_sample.json"
URL = "https://www.enforcementtracker.com/"


def fetch_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (research/data-sample; contact: research@jurismind.dev)"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def extract_json(html: str) -> list[dict]:
    # El tracker embebe los datos como un array JSON asignado a una variable JS
    # Buscamos patrones comunes: var data = [...], cases = [...], etc.
    patterns = [
        r'var\s+\w*[Dd]ata\w*\s*=\s*(\[.*?\])\s*;',
        r'var\s+\w*[Cc]ases\w*\s*=\s*(\[.*?\])\s*;',
        r'var\s+\w*[Ee]ntries\w*\s*=\s*(\[.*?\])\s*;',
        r'=\s*(\[\s*\{[^;]*"e"\s*:\s*1[^;]*\}.*?\])\s*[,;]',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

    # Fallback: buscar el array por el campo discriminador conocido ("e":1)
    m = re.search(r'(\[\s*\{"e"\s*:\s*1\b.*?\])', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    return []


def print_schema(records: list[dict]) -> None:
    print("\n--- CAMPOS OBSERVADOS ---")
    if not records:
        return
    # Union de todas las keys presentes
    all_keys: set[str] = set()
    for r in records:
        all_keys.update(r.keys())

    field_map = {
        "e": "ID secuencial",
        "c": "País (código)",
        "C": "País (nombre)",
        "F": "Flag image path",
        "a": "Autoridad DPA",
        "d": "Fecha decisión",
        "y": "Año",
        "f": "Multa EUR",
        "p": "Empresa / controlador",
        "s": "Sector",
        "r": "Artículos GDPR infringidos",
        "t": "Tipo de infracción",
        "u": "URL documento oficial",
    }
    for k in sorted(all_keys):
        desc = field_map.get(k, "—")
        sample_val = next((r[k] for r in records if k in r and r[k]), None)
        print(f"  {k:3s}  {desc:<35s}  ejemplo: {str(sample_val)[:60]!r}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Descargando {URL} ...")
    try:
        html = fetch_html(URL)
    except requests.RequestException as e:
        print(f"ERROR al descargar: {e}")
        sys.exit(1)

    print(f"HTML descargado ({len(html):,} bytes). Extrayendo JSON...")
    records = extract_json(html)

    if not records:
        print("ERROR: No se encontró el JSON embebido. El site puede haber cambiado su estructura.")
        print("Guardando HTML raw para inspección manual...")
        (OUT_DIR / "enforcement_tracker_raw.html").write_text(html, encoding="utf-8")
        sys.exit(1)

    print(f"Total de registros encontrados: {len(records):,}")

    sample = records[:SAMPLE_SIZE]
    OUT_FILE.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Sample ({SAMPLE_SIZE} registros) guardado en: {OUT_FILE}")

    print_schema(sample)

    print("\n--- PRIMEROS 2 REGISTROS ---")
    for r in sample[:2]:
        print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
