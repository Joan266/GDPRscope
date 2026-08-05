"""
JurisMind — Generador de índice del corpus

Corre queries SQL de stats sobre documents y genera data/corpus_index.json.
El índice se inyecta en el system prompt del RAG para que el LLM sepa qué hay disponible.

Relanzar cada vez que se ingestán nuevos documentos.

Uso:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python db/build_corpus_index.py
"""

import json
import logging
import os
import sys
from pathlib import Path

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OUTPUT_PATH  = Path(__file__).parent.parent / "data" / "corpus_index.json"

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def build_index(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        # Total docs
        cur.execute("SELECT COUNT(*) FROM documents")
        total_docs: int = cur.fetchone()[0]

        # Year range
        cur.execute(
            "SELECT MIN(decision_year), MAX(decision_year) FROM documents "
            "WHERE decision_year IS NOT NULL"
        )
        year_min, year_max = cur.fetchone()

        # By authority (top 20)
        cur.execute("""
            SELECT COALESCE(authority_abbrev, authority, 'Unknown'), COUNT(*)
            FROM documents
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 20
        """)
        authorities: dict = {row[0]: row[1] for row in cur.fetchall()}

        # Fine stats
        cur.execute("""
            SELECT MIN(fine_amount), MAX(fine_amount), COUNT(*)
            FROM documents
            WHERE fine_amount IS NOT NULL AND fine_amount > 0
        """)
        fine_min, fine_max, docs_with_fine = cur.fetchone()

        # Top GDPR articles (unnest array column)
        cur.execute("""
            SELECT article, COUNT(*) AS cnt
            FROM documents, unnest(gdpr_articles) AS article
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 10
        """)
        top_articles: list[str] = [row[0] for row in cur.fetchall()]

        # By source
        cur.execute(
            "SELECT source, COUNT(*) FROM documents GROUP BY 1 ORDER BY 2 DESC"
        )
        sources: dict = {row[0]: row[1] for row in cur.fetchall()}

        # Top sectors
        cur.execute("""
            SELECT sector, COUNT(*) AS cnt
            FROM documents
            WHERE sector IS NOT NULL
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 10
        """)
        top_sectors: list[str] = [row[0] for row in cur.fetchall()]

    return {
        "total_docs":       total_docs,
        "date_range":       f"{year_min}\u2013{year_max}" if year_min else "unknown",
        "authorities":      authorities,
        "fine_range_eur":   {
            "min":           int(fine_min)       if fine_min       else None,
            "max":           int(fine_max)       if fine_max       else None,
            "docs_with_fine": int(docs_with_fine) if docs_with_fine else 0,
        },
        "top_gdpr_articles": top_articles,
        "top_sectors":       top_sectors,
        "sources":           sources,
    }


def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        log.error("  export DATABASE_URL='postgresql://...'")
        sys.exit(1)

    log.info("Conectando a CockroachDB...")
    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    log.info("Generando indice del corpus...")
    index = build_index(conn)
    conn.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log.info("Escrito en %s", OUTPUT_PATH)
    log.info("  Total docs  : %d", index["total_docs"])
    log.info("  Rango anos  : %s", index["date_range"])
    log.info("  Authorities : %s", list(index["authorities"].keys())[:5])
    fine = index["fine_range_eur"]
    log.info(
        "  Fine range  : EUR %s - EUR %s (%d docs con multa)",
        fine["min"], fine["max"], fine["docs_with_fine"],
    )
    log.info("  Top articles: %s", index["top_gdpr_articles"][:5])


if __name__ == "__main__":
    main()
