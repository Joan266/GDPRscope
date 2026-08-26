"""
JurisMind — Citation graph builder

Extrae referencias cruzadas entre documentos GDPR usando regex sobre
el texto de los chunks parent, e inserta en la tabla `citations`.

Patrones reconocidos:
  EXP202310840             — AEPD expedient
  PS/00408/2019            — AEPD procedimiento sancionador (slash o guion)
  TD/00261/2020            — AEPD tutela de derechos
  R/00101/2021             — AEPD resolución
  AN-0000104/2021          — Audiencia Nacional (Spain)
  DOS-2020-00200           — APD/GBA Belgium
  C-311/18, C311/18        — CJEU case number

Idempotente: ON CONFLICT DO NOTHING — seguro de relanzar.

Uso:
    DATABASE_URL=... python db/build_citations.py --dry-run
    DATABASE_URL=... python db/build_citations.py
    DATABASE_URL=... python db/build_citations.py --limit 200
"""

import argparse
import logging
import os
import re
import sys

import psycopg

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# Confidence score for regex-based extraction (below human/LLM but reliable)
CONFIDENCE_REGEX = 0.85

# Regex covering the most common GDPR case number formats across EU authorities
_CASE_REF_PATTERN = re.compile(
    r"\b(?:"
    r"EXP\d{9,12}"                           # EXP202310840 (AEPD)
    r"|(?:PS|TD|R|AN)[-/\s]\d{4,8}[-/]\d{4}"  # PS/00408/2019, TD/00261/2020
    r"|DOS-\d{4}-\d{4,8}"                    # DOS-2020-00200 (APD/GBA)
    r"|C-\d{1,5}/\d{2,4}"                    # C-311/18 (CJEU joined/single)
    r")",
    re.IGNORECASE,
)

# ── DB helpers ─────────────────────────────────────────────────────────────────

def _load_case_number_index(cur: psycopg.Cursor) -> dict[str, str]:
    """
    Returns {normalised_case_number: document_id} for all documents with a case_number.
    Normalisation: uppercase, collapse whitespace, strip.
    """
    cur.execute(
        "SELECT id::TEXT, case_number FROM documents "
        "WHERE case_number IS NOT NULL"
    )
    index: dict[str, str] = {}
    for doc_id, cn in cur.fetchall():
        key = _normalise(cn)
        if key:
            index[key] = doc_id
    return index


def _normalise(case_number: str) -> str:
    """Uppercase + collapse all whitespace into nothing for fuzzy matching."""
    return re.sub(r"\s+", "", case_number.upper())


def _fetch_doc_text(cur: psycopg.Cursor, doc_id: str) -> str:
    """Concatenates parent chunk content for a given document."""
    cur.execute(
        "SELECT content FROM chunks "
        "WHERE document_id = %s AND chunk_type = 'parent' "
        "ORDER BY created_at",
        (doc_id,),
    )
    return " ".join(row[0] for row in cur.fetchall() if row[0])


def _extract_refs(text: str, index: dict[str, str], self_id: str) -> list[tuple[str, str]]:
    """
    Finds case-number references in `text` and resolves them against `index`.
    Returns list of (cited_doc_id, matched_text), excluding self-references.
    Deduplicates — each cited doc appears at most once per source doc.
    """
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for m in _CASE_REF_PATTERN.finditer(text):
        raw = m.group(0)
        key = _normalise(raw)
        cited_id = index.get(key)
        if cited_id and cited_id != self_id and cited_id not in seen:
            seen.add(cited_id)
            results.append((cited_id, raw))
    return results


def _insert_citation(
    cur: psycopg.Cursor,
    citing_id: str,
    cited_id: str,
    context_excerpt: str,
    dry_run: bool,
) -> bool:
    """Inserts one citation row. Returns True if a new row was created."""
    if dry_run:
        return True
    cur.execute(
        """
        INSERT INTO citations
          (citing_document_id, cited_document_id, relation_type,
           citation_context, extraction_method, confidence)
        VALUES (%s, %s, 'cites', %s, 'regex', %s)
        ON CONFLICT (citing_document_id, cited_document_id, relation_type)
        DO NOTHING
        """,
        (citing_id, cited_id, context_excerpt[:500], CONFIDENCE_REGEX),
    )
    return cur.rowcount == 1


# ── Main ───────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    log.info("Conectado a CockroachDB.")

    with conn.cursor() as cur:
        # 1. Build lookup index of all known case numbers
        case_index = _load_case_number_index(cur)
        log.info("Índice: %d case numbers en documentos.", len(case_index))

        # 2. Fetch all doc IDs to process
        cur.execute(
            "SELECT id::TEXT FROM documents ORDER BY ingested_at LIMIT %s",
            (args.limit,),
        )
        doc_ids = [row[0] for row in cur.fetchall()]
        log.info("Documentos a procesar: %d", len(doc_ids))

    n_inserted = n_found = n_docs_with_refs = 0

    with conn.cursor() as cur:
        for doc_id in doc_ids:
            text = _fetch_doc_text(cur, doc_id)
            if not text:
                continue

            refs = _extract_refs(text, case_index, doc_id)
            if not refs:
                continue

            n_docs_with_refs += 1
            n_found += len(refs)

            for cited_id, raw_match in refs:
                # Use a short snippet around the match for citation_context
                idx = text.find(raw_match)
                excerpt = text[max(0, idx - 60): idx + len(raw_match) + 60].strip()

                inserted = _insert_citation(cur, doc_id, cited_id, excerpt, args.dry_run)
                if inserted:
                    n_inserted += 1
                    if args.verbose:
                        log.info("  %s → %s  (%s)", doc_id[:8], cited_id[:8], raw_match)

    conn.close()

    log.info("=" * 55)
    log.info("%s terminado.", "DRY-RUN" if args.dry_run else "Completado")
    log.info("  Docs con referencias: %d", n_docs_with_refs)
    log.info("  Referencias encontradas: %d", n_found)
    log.info("  Filas insertadas en citations: %d", 0 if args.dry_run else n_inserted)
    if args.dry_run:
        log.info("  Relanza sin --dry-run para insertar.")


def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Citation graph builder")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar referencias sin insertar en DB")
    parser.add_argument("--limit", type=int, default=10_000,
                        help="Máximo de documentos a procesar (default: todos)")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar cada par (citing, cited) encontrado")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
