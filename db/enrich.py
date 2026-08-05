"""
JurisMind — HyPE (Hypothetical Prompt Embeddings) — implementación correcta

Genera preguntas hipotéticas POR CHILD CHUNK (no por documento) usando Claude Haiku
en batches de 5 chunks por llamada. Las preguntas se almacenan como chunks
separados (section='enrichment') y se buscan en un carril propio del RRF
(search_question_chunks), sin contaminar la búsqueda vectorial de chunks originales.

Arquitectura:
  query → 4-way RRF:
    carril 1: vector_chunks   (embeddings originales facts/dispute)
    carril 2: question_chunks (embeddings de preguntas HyPE)  ← este script
    carril 3: text_hits       (BM25 tsvector)
    carril 4: fine_sort       (multas DESC)

Coste estimado: ~$0.40-0.50 para 5,675 child chunks (2 preguntas/chunk, batch 5)
Tiempo:  ~10 min generación (Haiku) + ~40 min embedding (e5-large-v2 CPU)

Uso:
    python db/enrich.py                  # todos los chunks sin enriquecer
    python db/enrich.py --limit 100      # primeros 100 chunks
    python db/enrich.py --dry-run        # sin escribir en DB
    python db/enrich.py --source aepd    # solo fuente AEPD
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import psycopg
from anthropic import Anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HAIKU_MODEL       = "claude-haiku-4-5-20251001"
N_QUESTIONS       = 2   # preguntas por chunk — balance precisión/coste
BATCH_SIZE        = 5   # chunks por llamada Haiku — reduce coste 5x

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# Prompt de batch: genera N preguntas por cada uno de los M chunks del batch
_BATCH_PROMPT = """\
You are building a GDPR case law search engine.
For each numbered document excerpt below, generate exactly {n} questions \
that a DPO or privacy lawyer would ask whose COMPLETE answer is found \
ONLY in that excerpt (not inferred from general GDPR knowledge).
Focus on: specific entities, fine amounts, articles violated, dates, sectors.

Output ONLY a JSON array with one object per excerpt:
[{{"id": "CHUNK_ID", "questions": ["question 1", "question 2"]}}, ...]

{chunks}"""

_INSERT_ENRICHMENT = """
INSERT INTO chunks (id, document_id, chunk_type, parent_id, chunk_index,
                    content, content_tokens, section, search_vector)
VALUES (%s, %s, 'child', %s, %s, %s, %s, 'enrichment',
        to_tsvector('english', %s))
ON CONFLICT (id) DO NOTHING
"""


def _format_batch(chunks: list[dict]) -> str:
    """Formatea los chunks para el prompt batch."""
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"Excerpt {i} (ID: {c['id']}):\n"
            f"[{c.get('doc_title', '')} | {c.get('section', '')}]\n"
            f"{(c.get('content') or '')[:300]}"
        )
    return "\n\n---\n\n".join(parts)


def generate_questions_batch(
    ac: Anthropic,
    chunks: list[dict],
) -> dict[str, list[str]]:
    """
    Genera preguntas para un batch de chunks en una sola llamada Haiku.
    Devuelve dict {chunk_id: [pregunta1, pregunta2]}.
    """
    prompt = _BATCH_PROMPT.format(
        n=N_QUESTIONS,
        chunks=_format_batch(chunks),
    )
    msg = ac.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    results = json.loads(raw.strip())

    output: dict[str, list[str]] = {}
    for item in results:
        cid = str(item.get("id", ""))
        qs = [q for q in (item.get("questions") or []) if isinstance(q, str) and q.strip()]
        if cid and qs:
            output[cid] = qs[:N_QUESTIONS]
    return output


def insert_questions(
    cur: psycopg.Cursor,
    chunk: dict,
    questions: list[str],
) -> int:
    """Inserta preguntas HyPE como chunks de enriquecimiento. Devuelve n insertados."""
    doc_id    = chunk["document_id"]
    parent_id = chunk["parent_id"]

    for i, question in enumerate(questions):
        cur.execute(_INSERT_ENRICHMENT, (
            str(uuid.uuid4()),
            doc_id,
            parent_id,       # mismo parent que el chunk original → fetch_parent_context funciona
            10000 + i,       # chunk_index alto para no colisionar con chunks reales
            question,
            len(question.split()),
            question,
        ))
    return len(questions)


def main() -> None:
    parser = argparse.ArgumentParser(description="HyPE enrichment — preguntas por child chunk")
    parser.add_argument("--limit",   type=int, default=None, help="Máximo de chunks a enriquecer")
    parser.add_argument("--source",  default=None,           help="Filtrar por fuente (e.g. 'gdprhub')")
    parser.add_argument("--dry-run", action="store_true",    help="Sin escribir en DB")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL no definida")
    if not ANTHROPIC_API_KEY:
        sys.exit("ERROR: ANTHROPIC_API_KEY no definida")

    ac   = Anthropic(api_key=ANTHROPIC_API_KEY)
    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    # Obtener child chunks con embedding que aún no tienen preguntas HyPE
    src_filter = "AND d.source = %s" if args.source else ""
    src_param  = [args.source] if args.source else []
    limit_sql  = f"LIMIT {args.limit}" if args.limit else ""

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT c.id, c.document_id, c.parent_id, c.content, c.section,
                   d.title AS doc_title, d.source
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.chunk_type = 'child'
              AND c.section != 'enrichment'
              AND c.embedding IS NOT NULL
              AND c.content IS NOT NULL AND c.content != ''
              AND NOT EXISTS (
                  SELECT 1 FROM chunks e
                  WHERE e.parent_id = c.parent_id
                    AND e.section = 'enrichment'
              )
            {src_filter}
            ORDER BY c.id
            {limit_sql}
        """, src_param)
        rows = cur.fetchall()

    chunks = [
        {
            "id":          str(r[0]),
            "document_id": str(r[1]),
            "parent_id":   str(r[2]) if r[2] else None,
            "content":     r[3],
            "section":     r[4],
            "doc_title":   r[5],
            "source":      r[6],
        }
        for r in rows
        if r[2]  # necesita parent_id
    ]

    log.info("Child chunks a enriquecer: %d (batches de %d → %d llamadas Haiku)",
             len(chunks), BATCH_SIZE, (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE)

    if not chunks:
        log.info("Nada que hacer.")
        conn.close()
        return

    total_q   = 0
    total_err = 0

    with conn.cursor() as cur:
        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE

            try:
                questions_map = generate_questions_batch(ac, batch)
            except Exception as exc:
                log.warning("[batch %d/%d] Error Haiku: %s", batch_num, total_batches, exc)
                total_err += len(batch)
                continue

            for chunk in batch:
                qs = questions_map.get(chunk["id"], [])
                if not qs:
                    log.debug("  Sin preguntas para chunk %s", chunk["id"][:8])
                    continue

                if args.dry_run:
                    log.info("  [dry-run] chunk %s → %d preguntas", chunk["id"][:8], len(qs))
                    for q in qs:
                        log.info("    · %s", q)
                    total_q += len(qs)
                else:
                    total_q += insert_questions(cur, chunk, qs)

            if batch_num % 20 == 0:
                log.info("Progreso: %d/%d batches | %d preguntas generadas | %d errores",
                         batch_num, total_batches, total_q, total_err)

    conn.close()
    action = "generadas (dry-run)" if args.dry_run else "insertadas en DB"
    log.info("Listo. %d preguntas HyPE %s (%d errores).", total_q, action, total_err)
    if not args.dry_run:
        log.info("Siguiente paso: python db/embed.py --sections enrichment")


if __name__ == "__main__":
    main()
