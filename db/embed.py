"""
JurisMind — Embedding generator

Lee chunks sin embedding de CockroachDB, genera vectores con
sentence-transformers (intfloat/e5-large-v2, 1024 dims) y los guarda.

Estrategia:
  - Se embeben TODOS los chunks (parent y child).
  - El motor RAG usa child chunks para retrieval y parent chunks para contexto al LLM.
  - Para documentos sin children (tracker, eurlex), el parent ES la unidad de retrieval.
  - Idempotente: WHERE embedding IS NULL — seguro de relanzar si se interrumpe.

Uso:
    DATABASE_URL=... python db/embed.py
    DATABASE_URL=... python db/embed.py --batch-size 200
    DATABASE_URL=... python db/embed.py --dry-run      # cuenta sin embeber
    DATABASE_URL=... python db/embed.py --source gdprhub  # solo una fuente
"""

import argparse
import logging
import os
import sys
import time

import psycopg

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
MODEL_NAME        = "intfloat/e5-large-v2"   # 1024 dims, mismo que Titan V2
EMBEDDING_VERSION = "e5-large-v2-1024"
EMBED_DIMS        = 1024
BATCH_SIZE        = 100     # chunks por commit de DB
MAX_CHARS         = 8_000   # e5-large-v2 max ~512 tokens ≈ 8K chars

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Sentence Transformers ───────────────────────────────────────────────────────

_st_model = None


def get_model():
    """Lazy-load del modelo — se descarga una sola vez (~1.3GB)."""
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer
        log.info("Cargando modelo %s (primera vez: descarga ~1.3GB)...", MODEL_NAME)
        _st_model = SentenceTransformer(MODEL_NAME)
        log.info("Modelo cargado.")
    return _st_model


def embed_text(text: str) -> list[float]:
    """Genera embedding de 1024 floats con e5-large-v2 (L2 normalizado).
    e5-large-v2 requiere prefijo 'passage: ' en documentos."""
    text = ("passage: " + text[:MAX_CHARS]).strip()
    if not text:
        raise ValueError("Texto vacío — no se puede embeber")
    model = get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_texts_batch(texts: list[str]) -> list[list[float]]:
    """Batch embedding — 10-30x faster than one-by-one on CPU."""
    prefixed = [("passage: " + t[:MAX_CHARS]).strip() for t in texts]
    model = get_model()
    vecs = model.encode(prefixed, normalize_embeddings=True, batch_size=64,
                        show_progress_bar=False)
    return [v.tolist() for v in vecs]


def vector_to_pg(embedding: list[float]) -> str:
    """Serializa vector a formato literal de PostgreSQL/CockroachDB VECTOR."""
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


# ── DB helpers ─────────────────────────────────────────────────────────────────

_SELECT_PENDING = """
SELECT c.id, c.content, c.chunk_type, c.section, d.source,
       d.title, d.controller_name, d.gdpr_articles
FROM   chunks c
JOIN   documents d ON d.id = c.document_id
WHERE  c.embedding IS NULL
  AND  c.content IS NOT NULL
  AND  length(c.content) > 20
{source_filter}
{section_filter}
{fines_filter}
ORDER BY c.created_at
LIMIT  %s
"""

_UPDATE_CHUNK = """
UPDATE chunks
SET    embedding         = %s::VECTOR({dims}),
       embedding_model   = %s,
       embedding_version = %s,
       embedded_at       = now()
WHERE  id = %s
""".format(dims=EMBED_DIMS)

def _section_filter_sql(sections: list[str] | None, alias: str = "c") -> tuple[str, list]:
    """Construye cláusula AND section IN (...) y devuelve (sql, params)."""
    if not sections:
        return "", []
    col = f"{alias}.section" if alias else "section"
    placeholders = ",".join(["%s"] * len(sections))
    return f"AND {col} IN ({placeholders})", list(sections)


def count_chunks(conn: psycopg.Connection, sections: list[str] | None = None,
                 fines_only: bool = False) -> tuple[int, int]:
    """Devuelve (pendientes, total), opcionalmente filtrado por sección."""
    sec_sql, sec_params = _section_filter_sql(sections, alias="c")
    fines_sql = "AND d.fine_amount > 0" if fines_only else ""
    base = (f"FROM chunks c JOIN documents d ON d.id = c.document_id "
            f"WHERE length(coalesce(c.content,'')) > 20 {sec_sql} {fines_sql}")
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) {base} AND c.embedding IS NULL", sec_params)
        pending = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) {base}", sec_params)
        total = cur.fetchone()[0]
    return pending, total


def fetch_pending(cur: psycopg.Cursor, batch: int, source: str | None,
                   sections: list[str] | None, fines_only: bool = False) -> list[tuple]:
    # Parameterized source filter — never interpolate user/external values into SQL strings.
    src_sql, src_params = ("AND d.source = %s", [source]) if source else ("", [])
    sec_sql, sec_params = _section_filter_sql(sections, alias="c")
    fines_sql = "AND d.fine_amount > 0" if fines_only else ""
    cur.execute(
        _SELECT_PENDING.format(source_filter=src_sql, section_filter=sec_sql,
                               fines_filter=fines_sql),
        src_params + sec_params + [batch],
    )
    return cur.fetchall()


# ── Main loop ──────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        log.error("  export DATABASE_URL='postgresql://user:pass@host:26257/jurismind?sslmode=verify-full'")
        sys.exit(1)

    log.info("Conectando a CockroachDB...")
    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    sections = None if args.sections == "all" else args.sections.split(",")
    if sections:
        log.info("Secciones a embeber: %s", ", ".join(sections))
    else:
        log.info("Embebiendo TODAS las secciones (--sections all)")

    pending, total = count_chunks(conn, sections, args.fines_only)
    done = total - pending
    log.info("Chunks: %d total — %d ya embebidos — %d pendientes", total, done, pending)

    if pending == 0:
        log.info("Nada que embeber.")
        conn.close()
        return

    if args.dry_run:
        log.info("--dry-run: sin embeddings ni escrituras.")
        conn.close()
        return

    log.info("Inicializando modelo de embeddings: %s...", MODEL_NAME)
    get_model()   # pre-carga para que el ETA sea preciso
    log.info("Coste estimado: $0.00 (sentence-transformers es local y gratuito)")

    n_ok = n_err = 0
    t_start = time.monotonic()

    with conn.cursor() as cur:
        while True:
            rows = fetch_pending(cur, args.batch_size, args.source, sections, args.fines_only)
            if not rows:
                break

            # Prepare enriched texts for batch encoding
            enriched_texts: list[str] = []
            row_meta: list[tuple] = []  # (chunk_id, source, section)
            for chunk_id, content, chunk_type, section, source, title, controller, articles in rows:
                articles_str = ", ".join(articles) if articles else ""
                meta_prefix = " | ".join(filter(None, [title, controller, articles_str]))
                enriched = f"{meta_prefix}\n{content}" if meta_prefix else content
                enriched_texts.append(enriched)
                row_meta.append((chunk_id, source, section))

            # Batch encode all texts at once (10-30x faster than one-by-one)
            try:
                vectors = embed_texts_batch(enriched_texts)
            except Exception as e:
                log.warning("Batch encode failed (%d texts): %s", len(enriched_texts), e)
                n_err += len(enriched_texts)
                continue

            batch_updates: list[tuple] = []
            for (chunk_id, source, section), vector in zip(row_meta, vectors):
                vector_pg = vector_to_pg(vector)
                batch_updates.append((vector_pg, MODEL_NAME, EMBEDDING_VERSION, chunk_id))
                n_ok += 1

            if batch_updates:
                cur.executemany(_UPDATE_CHUNK, batch_updates)

            elapsed  = time.monotonic() - t_start
            rate     = n_ok / elapsed if elapsed > 0 else 0
            remaining = (pending - n_ok - n_err) / rate if rate > 0 else 0
            log.info(
                "Progreso: %d/%d embebidos | %d errores | %.1f/s | ETA: %.0f min",
                n_ok, pending, n_err, rate, remaining / 60,
            )

    elapsed_total = time.monotonic() - t_start
    conn.close()

    log.info("=" * 55)
    log.info("Completado en %.0f min", elapsed_total / 60)
    log.info("  Exitosos: %d", n_ok)
    log.info("  Errores:  %d", n_err)
    if n_err > 0:
        log.info("  Relanza el script para reintentar los fallidos (idempotente).")
    log.info("Siguiente paso: python db/rag.py  (motor de búsqueda)")


def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Embedding generator")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                        help=f"Chunks por commit (default: {BATCH_SIZE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Contar pendientes sin llamar a Bedrock")
    parser.add_argument("--source", choices=["gdprhub", "enforcement_tracker", "eurlex"],
                        default=None, help="Embeber solo una fuente")
    parser.add_argument("--sections", default="teaser,facts,dispute,headnote",
                        help="Secciones a embeber, separadas por coma. "
                             "'all' = sin filtro (default: teaser,facts,dispute,headnote)")
    parser.add_argument("--fines-only", action="store_true",
                        help="Solo embeber chunks de documentos con fine_amount > 0")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
