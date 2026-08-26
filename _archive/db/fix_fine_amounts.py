"""
Fix puntual: actualiza fine_amount en los documentos donde el parser falló
por recibir el valor en formato float ("20000.0") en lugar de entero.

Solo hace UPDATE en documents — no toca chunks ni embeddings.
Consume mínimo de RUs (5 reads GDPRhub API + 5 SQL UPDATE).

Uso:
    DATABASE_URL=... python db/fix_fine_amounts.py [--dry-run]
"""

import argparse
import logging
import os
import re
import sys

import psycopg
import requests

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GDPRHUB_API  = "https://gdprhub.eu/api.php"
HEADERS      = {"User-Agent": "JurisMind/1.0 (research@jurismind.dev)"}

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Casos a corregir — source_id exacto tal como está en la DB
# Valores verificados manualmente en audit_aepd.md (2026-08-02)
# GDPRhub no siempre rellena |Fine= en el template aunque mencione la multa en el texto
TARGET_CASES = [
    "AEPD (Spain) - EXP202411411",
    "AEPD (Spain) - PS-00143-2025",
    "AEPD (Spain) - PS-00304-2024",
    "AEPD (Spain) - PS-00005-2025",
    "AEPD (Spain) - EXP202404507",
]

# Fallback: valores verificados en el teaser/holding cuando el campo |Fine= está vacío
KNOWN_FINES: dict[str, tuple[int, str]] = {
    "AEPD (Spain) - EXP202411411": (200_000, "EUR"),   # "fined €200,000" — teaser confirmado
    "AEPD (Spain) - PS-00143-2025": (400_000, "EUR"),  # "fined a bank €400,000" — teaser confirmado
    "AEPD (Spain) - PS-00005-2025": (14_400_000, "EUR"),  # "fined €14,400,000" — teaser confirmado
}


def fetch_fine_from_gdprhub(source_id: str) -> tuple[int | None, str | None]:
    """Obtiene Fine y Currency de GDPRhub para un source_id dado."""
    params = {
        "action": "parse",
        "page": source_id,
        "prop": "wikitext",
        "formatversion": "2",
    }
    try:
        r = requests.get(GDPRHUB_API, params={**params, "format": "json"},
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        wikitext = r.json().get("parse", {}).get("wikitext", "")
    except Exception as e:
        log.error("Error fetching %s: %s", source_id, e)
        return None, None

    fine_match     = re.search(r'\|\s*Fine\s*=\s*([^\n|}\]]+)', wikitext)
    currency_match = re.search(r'\|\s*Currency\s*=\s*([^\n|}\]]+)', wikitext)

    fine_str  = fine_match.group(1).strip() if fine_match else ""
    currency  = currency_match.group(1).strip() if currency_match else "EUR"

    try:
        fine_amount = int(float(fine_str)) if fine_str else None
    except (ValueError, TypeError):
        fine_amount = None

    return fine_amount, currency or "EUR"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix fine_amount en documentos GDPR")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra los valores pero no actualiza la DB")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL no definida.")

    log.info("Fetching fine amounts from GDPRhub...")
    updates: list[tuple[int, str, str]] = []

    for source_id in TARGET_CASES:
        fine, currency = fetch_fine_from_gdprhub(source_id)
        status = f"€{fine:,}" if fine else "NULL (no fine o no encontrado)"
        log.info("  %-45s → %s %s", source_id, status, currency or "")
        if fine is None and source_id in KNOWN_FINES:
            fine, currency = KNOWN_FINES[source_id]
            log.info("  %-45s → €%s %s (fallback desde audit)", source_id, f"{fine:,}", currency)
        if fine is not None:
            updates.append((fine, currency, source_id))

    if not updates:
        log.info("Nada que actualizar.")
        return

    if args.dry_run:
        log.info("DRY RUN — no se modifica la DB. %d casos a actualizar.", len(updates))
        return

    log.info("Actualizando %d documentos en CockroachDB...", len(updates))
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for fine_amount, currency, source_id in updates:
                cur.execute(
                    """UPDATE documents
                       SET fine_amount = %s, fine_currency = %s, updated_at = now()
                       WHERE source_id = %s""",
                    (fine_amount, currency, source_id),
                )
                log.info("  UPDATED: %s → €%s", source_id, f"{fine_amount:,}")
        conn.commit()

    log.info("Listo. %d documentos actualizados.", len(updates))


if __name__ == "__main__":
    main()
