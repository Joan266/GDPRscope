"""
JurisMind — Embedding generator

Lee chunks sin embedding de CockroachDB, llama a Amazon Bedrock
Titan Text Embeddings V2 y guarda los vectores de 1024 dims.

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
import json
import logging
import os
import sys
import time
from datetime import datetime

import boto3
import psycopg
from botocore.exceptions import ClientError

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
AWS_REGION        = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID          = "amazon.titan-embed-text-v2:0"
EMBEDDING_VERSION = "titan-v2-1024"
EMBED_DIMS        = 1024
BATCH_SIZE        = 100     # chunks por commit de DB
MAX_CHARS         = 30_000  # Titan soporta ~8192 tokens ≈ 32K chars; margen conservador
MAX_RETRIES       = 6
RETRY_BASE_SEC    = 1.0     # backoff exponencial: 1, 2, 4, 8, 16, 32s

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Bedrock ────────────────────────────────────────────────────────────────────

def make_bedrock_client() -> boto3.client:
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def embed_text(client, text: str) -> list[float]:
    """
    Llama a Titan Text Embeddings V2. Devuelve vector de 1024 floats.
    Aplica exponential backoff ante ThrottlingException.
    """
    text = text[:MAX_CHARS].strip()
    if not text:
        raise ValueError("Texto vacío — no se puede embeber")

    body = json.dumps({
        "inputText": text,
        "dimensions": EMBED_DIMS,
        "normalize":  True,   # L2 normalización — mejora cosine similarity
    })

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp   = client.invoke_model(
                modelId=MODEL_ID,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(resp["body"].read())
            return result["embedding"]

        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ThrottlingException", "ServiceUnavailableException"):
                delay = RETRY_BASE_SEC * (2 ** attempt)
                log.warning("Bedrock %s — esperando %.0fs (intento %d/%d)",
                            code, delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                last_error = e
                continue
            raise  # otros errores: no reintentar

    raise RuntimeError(f"Bedrock no respondió tras {MAX_RETRIES} intentos") from last_error


def vector_to_pg(embedding: list[float]) -> str:
    """Serializa vector a formato literal de PostgreSQL/CockroachDB VECTOR."""
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


# ── DB helpers ─────────────────────────────────────────────────────────────────

_SELECT_PENDING = """
SELECT c.id, c.content, c.chunk_type, c.section, d.source
FROM   chunks c
JOIN   documents d ON d.id = c.document_id
WHERE  c.embedding IS NULL
  AND  c.content IS NOT NULL
  AND  length(c.content) > 20
{source_filter}
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

_COUNT_PENDING = """
SELECT count(*) FROM chunks WHERE embedding IS NULL AND length(coalesce(content,'')) > 20
"""

_COUNT_TOTAL = "SELECT count(*) FROM chunks WHERE length(coalesce(content,'')) > 20"


def count_chunks(conn: psycopg.Connection) -> tuple[int, int]:
    """Devuelve (pendientes, total)."""
    with conn.cursor() as cur:
        cur.execute(_COUNT_PENDING)
        pending = cur.fetchone()[0]
        cur.execute(_COUNT_TOTAL)
        total = cur.fetchone()[0]
    return pending, total


def fetch_pending(cur: psycopg.Cursor, batch: int, source: str | None) -> list[tuple]:
    src_filter = f"AND d.source = '{source}'" if source else ""
    cur.execute(_SELECT_PENDING.format(source_filter=src_filter), (batch,))
    return cur.fetchall()


# ── Main loop ──────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        log.error("  export DATABASE_URL='postgresql://user:pass@host:26257/jurismind?sslmode=verify-full'")
        sys.exit(1)

    log.info("Conectando a CockroachDB...")
    conn = psycopg.connect(DATABASE_URL)

    pending, total = count_chunks(conn)
    done = total - pending
    log.info("Chunks: %d total — %d ya embebidos — %d pendientes", total, done, pending)

    if pending == 0:
        log.info("Nada que embeber.")
        conn.close()
        return

    if args.dry_run:
        log.info("--dry-run: sin llamadas a Bedrock ni escrituras.")
        conn.close()
        return

    log.info("Iniciando Bedrock client (region=%s, model=%s)...", AWS_REGION, MODEL_ID)
    client = make_bedrock_client()

    # Estimación de coste: Titan V2 = $0.00002 / 1K tokens; ~150 tokens/child chunk
    est_tokens = pending * 150
    est_cost   = est_tokens / 1_000 * 0.00002
    log.info("Coste estimado: ~$%.4f (%.0fK tokens aprox.)", est_cost, est_tokens / 1_000)

    n_ok = n_err = 0
    t_start = time.monotonic()

    with conn.cursor() as cur:
        while True:
            rows = fetch_pending(cur, args.batch_size, args.source)
            if not rows:
                break

            batch_updates: list[tuple] = []

            for chunk_id, content, chunk_type, section, source in rows:
                try:
                    vector    = embed_text(client, content)
                    vector_pg = vector_to_pg(vector)
                    batch_updates.append((vector_pg, MODEL_ID, EMBEDDING_VERSION, chunk_id))
                    n_ok += 1
                except Exception as e:
                    log.warning("chunk %s [%s/%s]: ERROR — %s", chunk_id, source, section, e)
                    n_err += 1
                    continue

            # Actualizar en batch
            if batch_updates:
                cur.executemany(_UPDATE_CHUNK, batch_updates)
                conn.commit()

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
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
