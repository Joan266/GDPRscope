"""
Sample downloader — EUR-Lex CELLAR (SPARQL)

Descarga una muestra de sentencias TJUE relacionadas con el GDPR.

Problema original: el script usaba URIs "celex/..." para cdm:work_cites_work,
pero CELLAR usa URIs "cellar/{UUID}". Fix: two-step lookup.

Enfoques:
  A. Two-step SPARQL:
     1. Buscar el UUID del GDPR via cdm:resource_legal_id_celex = "32016R0679"
     2. Buscar sentencias (CJ) que citen ese UUID con cdm:work_cites_work
  B. SPARQL: buscar sentencias CJ post-2018 que citen algún cellar del GDPR
     via CONTAINS en el celex del trabajo citado (mas tolerante)
  C. Fallback SPARQL: lookup directo de CELEX hardcodeados (landmark cases)

Output: data/samples/eurlex_gdpr_cases.json
"""

import json
import time
from pathlib import Path

import requests

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
OUT_DIR = Path(__file__).parent.parent / "data" / "samples"
OUT_FILE = OUT_DIR / "eurlex_gdpr_cases.json"

HEADERS_SPARQL = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "JurisMind-research/0.1 (contact: research@jurismind.dev)",
}

# GDPR CELEX
GDPR_CELEX = "32016R0679"

# Landmark GDPR cases ante el TJUE — CELEXes conocidos
LANDMARK_CELEX = [
    "62014CJ0362",  # Schrems I (C-362/14)
    "62018CJ0311",  # Schrems II (C-311/18)
    "62017CJ0673",  # Planet49 (C-673/17)
    "62017CJ0040",  # Fashion ID (C-40/17)
    "62019CJ0645",  # Facebook Ireland (C-645/19)
    "62021CJ0252",  # Meta/bundled consent (C-252/21)
    "62021CJ0300",  # Non-material damages (C-300/21)
]


def run_sparql(query: str) -> list[dict] | None:
    try:
        r = requests.post(
            SPARQL_ENDPOINT,
            data={"query": query},
            headers=HEADERS_SPARQL,
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("results", {}).get("bindings", [])
    except Exception as e:
        print(f"    SPARQL error: {e}")
        return None


def get_gdpr_cellar_uri() -> str | None:
    """Paso 1: obtener el URI cellar del GDPR via su CELEX."""
    query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?work WHERE {{
  ?work cdm:resource_legal_id_celex "{GDPR_CELEX}"^^<http://www.w3.org/2001/XMLSchema#string> .
}}
LIMIT 1
"""
    bindings = run_sparql(query)
    if bindings:
        return bindings[0]["work"]["value"]
    return None


def approach_a(gdpr_uri: str) -> list[dict] | None:
    """Sentencias TJUE que citan el GDPR via cdm:work_cites_work + URI cellar correcto."""
    print(f"  GDPR cellar URI: {gdpr_uri}")
    query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?work ?celex ?date ?ecli WHERE {{
  ?work cdm:work_cites_work <{gdpr_uri}> .
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(REGEX(STR(?celex), "^6[0-9]{{4}}CJ"))
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli }}
}}
ORDER BY DESC(?date)
LIMIT 20
"""
    bindings = run_sparql(query)
    if not bindings:
        return None
    return [
        {
            "celex": b.get("celex", {}).get("value", ""),
            "date":  b.get("date",  {}).get("value", ""),
            "ecli":  b.get("ecli",  {}).get("value", ""),
            "work":  b.get("work",  {}).get("value", ""),
            "approach": "A",
        }
        for b in bindings
    ]


def approach_b() -> list[dict] | None:
    """Sentencias TJUE post-GDPR, filtrando por celex con CONTAINS (más tolerante)."""
    query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?work ?celex ?date ?ecli WHERE {{
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(REGEX(STR(?celex), "^6[0-9]{{4}}CJ"))
  ?work cdm:work_cites_work ?cited .
  ?cited cdm:resource_legal_id_celex ?citedCelex .
  FILTER(?citedCelex = "{GDPR_CELEX}"^^<http://www.w3.org/2001/XMLSchema#string>)
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli }}
}}
ORDER BY DESC(?date)
LIMIT 20
"""
    bindings = run_sparql(query)
    if not bindings:
        return None
    return [
        {
            "celex": b.get("celex", {}).get("value", ""),
            "date":  b.get("date",  {}).get("value", ""),
            "ecli":  b.get("ecli",  {}).get("value", ""),
            "work":  b.get("work",  {}).get("value", ""),
            "approach": "B",
        }
        for b in bindings
    ]


def approach_c_sparql(celex_list: list[str]) -> list[dict]:
    """Fallback: lookup directo de CELEX landmark cases via SPARQL (confirmado funciona)."""
    results = []
    for celex in celex_list:
        query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?work ?date ?ecli WHERE {{
  ?work cdm:resource_legal_id_celex "{celex}"^^<http://www.w3.org/2001/XMLSchema#string> .
  OPTIONAL {{ ?work cdm:work_date_document ?date }}
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli }}
}}
LIMIT 1
"""
        bindings = run_sparql(query)
        if bindings:
            b = bindings[0]
            results.append({
                "celex": celex,
                "date":  b.get("date", {}).get("value", ""),
                "ecli":  b.get("ecli", {}).get("value", ""),
                "work":  b.get("work", {}).get("value", ""),
                "approach": "C",
            })
            print(f"    {celex}: found — {b.get('date', {}).get('value', 'no date')}")
        else:
            print(f"    {celex}: not found in CELLAR")
        time.sleep(0.3)
    return results


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    approach_used: str = ""

    # Step 1: obtener URI cellar del GDPR
    print("Buscando URI cellar del GDPR...")
    gdpr_uri = get_gdpr_cellar_uri()
    print(f"  {'OK: ' + gdpr_uri if gdpr_uri else 'NO encontrado — saltando enfoques A y B'}")

    # Enfoque A
    if gdpr_uri:
        print("\n[A] cdm:work_cites_work + filtro CJ...")
        records = approach_a(gdpr_uri)
        if records:
            results = records
            approach_used = "A"
            print(f"  Resultados: {len(results)}")

    # Enfoque B
    if not results:
        print("\n[B] CONTAINS filter sobre celex citado...")
        time.sleep(1)
        records = approach_b()
        if records:
            results = records
            approach_used = "B"
            print(f"  Resultados: {len(results)}")

    # Enfoque C
    if not results:
        print("\n[C] Fallback SPARQL landmark cases...")
        results = approach_c_sparql(LANDMARK_CELEX)
        approach_used = "C"

    print(f"\nEnfoque exitoso: {approach_used}")
    print(f"Registros obtenidos: {len(results)}")

    # Verificar Schrems II
    schrems_celex = "62018CJ0311"
    has_schrems = any(schrems_celex in r.get("celex", "") for r in results)
    print(f"Schrems II presente: {'SI' if has_schrems else 'NO'}")

    output = {
        "gdpr_cellar_uri": gdpr_uri,
        "approach_used": approach_used,
        "total": len(results),
        "schrems_ii_found": has_schrems,
        "records": results,
    }

    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado en: {OUT_FILE}")

    if results:
        print("\n--- PRIMEROS 5 REGISTROS ---")
        print(json.dumps(results[:5], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
