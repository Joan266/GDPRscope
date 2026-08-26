"""
Verificador — GDPR Enforcement Tracker en HuggingFace

Compara el dataset HuggingFace con nuestro scraping propio del enforcement tracker.

Fuente HF: Sebastyijan/gdpr-enforcement-sample
API: datasets-server.huggingface.co (sin auth, sin instalar `datasets`)

Métricas a comparar:
  - Número de filas
  - Campos disponibles
  - Campos adicionales vs nuestro scraping (e, c, C, a, d, f, r, t, u)

Output:
  - stdout: reporte de decisión (usar HF vs scraping propio)
  - data/samples/hf_tracker_comparison.json
"""

import json
from pathlib import Path

import requests

HF_API = "https://datasets-server.huggingface.co"
DATASET = "Sebastyijan/gdpr-enforcement-sample"
OUT_DIR = Path(__file__).parent.parent / "data" / "samples"
OUT_FILE = OUT_DIR / "hf_tracker_comparison.json"
OWN_SAMPLE = OUT_DIR / "enforcement_tracker_sample.json"

HEADERS = {"User-Agent": "JurisMind-research/0.1 (contact: research@jurismind.dev)"}

# Campos que tenemos en nuestro scraping propio
OWN_FIELDS = {"e", "c", "C", "a", "d", "f", "r", "t", "u"}

# Total de registros en nuestro scraping
OWN_TOTAL = 3202


def fetch_dataset_info() -> dict | None:
    url = f"{HF_API}/info?dataset={DATASET}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [info] Error: {e}")
        return None


def fetch_dataset_rows(split: str = "train", limit: int = 20) -> dict | None:
    url = f"{HF_API}/rows?dataset={DATASET}&split={split}&offset=0&limit={limit}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [rows] Error: {e}")
        return None


def fetch_dataset_size() -> int | None:
    """Intenta obtener el número total de filas via /size endpoint."""
    url = f"{HF_API}/size?dataset={DATASET}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        # La respuesta tiene size.dataset_size o splits[].num_rows
        size_data = data.get("size", {})
        splits = size_data.get("splits", [])
        if splits:
            return sum(s.get("num_rows", 0) for s in splits)
        return None
    except Exception as e:
        print(f"  [size] Error: {e}")
        return None


def load_own_sample() -> list[dict]:
    if OWN_SAMPLE.exists():
        return json.loads(OWN_SAMPLE.read_text(encoding="utf-8"))
    return []


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Verificando dataset HuggingFace: {DATASET}")
    print("=" * 60)

    result: dict = {
        "dataset": DATASET,
        "own_total": OWN_TOTAL,
        "own_fields": sorted(OWN_FIELDS),
    }

    # 1. Info del dataset
    print("\n[1] Fetching dataset info...")
    info = fetch_dataset_info()
    if info:
        configs = info.get("dataset_info", {})
        result["hf_info"] = configs
        print(f"  Configs disponibles: {list(configs.keys()) if isinstance(configs, dict) else 'n/a'}")
    else:
        print("  No disponible")
        result["hf_info"] = None

    # 2. Tamaño total
    print("\n[2] Fetching dataset size...")
    hf_total = fetch_dataset_size()
    result["hf_total"] = hf_total
    if hf_total is not None:
        print(f"  Filas HF: {hf_total:,}")
        print(f"  Filas propias: {OWN_TOTAL:,}")
        delta = hf_total - OWN_TOTAL
        print(f"  Delta: {delta:+,}")
    else:
        print("  No disponible")

    # 3. Primeras filas
    print("\n[3] Fetching primeras 20 filas...")
    rows_data = fetch_dataset_rows(limit=20)
    hf_fields: set[str] = set()
    sample_rows: list[dict] = []

    if rows_data:
        rows = rows_data.get("rows", [])
        print(f"  Filas obtenidas: {len(rows)}")
        for row_wrapper in rows:
            row = row_wrapper.get("row", row_wrapper)
            sample_rows.append(row)
            hf_fields.update(row.keys())

        print(f"  Campos HF: {sorted(hf_fields)}")
    else:
        print("  No disponible")

    result["hf_fields"] = sorted(hf_fields)

    # 4. Análisis de campos
    extra_in_hf = hf_fields - OWN_FIELDS
    missing_in_hf = OWN_FIELDS - hf_fields
    result["extra_in_hf"] = sorted(extra_in_hf)
    result["missing_in_hf"] = sorted(missing_in_hf)
    result["hf_sample_rows"] = sample_rows[:5]

    print("\n[4] Análisis de campos:")
    print(f"  Campos solo en HF (nuevos): {sorted(extra_in_hf) or 'ninguno'}")
    print(f"  Campos solo en propio:       {sorted(missing_in_hf) or 'ninguno'}")

    # 5. Carga de nuestra muestra propia para comparación de valores
    own_sample = load_own_sample()
    if own_sample and sample_rows:
        print("\n[5] Comparación de valores (primer registro):")
        print(f"  HF[0]:    {json.dumps(sample_rows[0], ensure_ascii=False)[:200]}")
        print(f"  Propio[0]: {json.dumps(own_sample[0], ensure_ascii=False)[:200]}")

    # 6. Decisión
    print("\n" + "=" * 60)
    print("DECISION:")
    hf_available = rows_data is not None

    if not hf_available:
        decision = "SCRAPING_PROPIO"
        reason = "Dataset HF no accesible o no existe"
    elif hf_total is not None and hf_total > OWN_TOTAL * 1.05:
        decision = "USAR_HF"
        reason = f"HF tiene {hf_total:,} filas vs {OWN_TOTAL:,} propias (+{hf_total-OWN_TOTAL:,})"
    elif extra_in_hf:
        decision = "EVALUAR_HF"
        reason = f"HF tiene campos adicionales: {sorted(extra_in_hf)}"
    else:
        decision = "SCRAPING_PROPIO"
        reason = f"HF no aporta ventaja (filas: {hf_total}, campos extra: ninguno)"

    result["decision"] = decision
    result["decision_reason"] = reason
    print(f"  {decision}: {reason}")

    # Guardar
    OUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReporte guardado en: {OUT_FILE}")


if __name__ == "__main__":
    main()
