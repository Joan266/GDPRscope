"""
Import BGE-M3 embeddings from Colab output into PostgreSQL.

Reads embeddings_bge_m3.npy + chunk_ids.npy and updates the chunks table.

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python db/import_embeddings.py --embeddings eval/embeddings_bge_m3.npy --ids eval/chunk_ids.npy
"""

import argparse
import logging
import os
import sys
import time

import numpy as np
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_SIZE = 100
MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_VERSION = "bge-m3-1024"

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def import_embeddings(conn: psycopg.Connection, embeddings: np.ndarray, chunk_ids: np.ndarray) -> None:
    """Update chunks with BGE-M3 embeddings."""
    assert len(embeddings) == len(chunk_ids), f"Mismatch: {len(embeddings)} embeddings vs {len(chunk_ids)} IDs"
    dims = embeddings.shape[1]
    log.info("Importing %d embeddings (%d dims) into chunks table", len(embeddings), dims)

    updated = 0
    errors = 0
    start = time.time()

    with conn.cursor() as cur:
        for i in range(0, len(chunk_ids), BATCH_SIZE):
            batch_ids = chunk_ids[i:i + BATCH_SIZE]
            batch_embs = embeddings[i:i + BATCH_SIZE]

            for chunk_id, emb in zip(batch_ids, batch_embs):
                try:
                    vec_str = "[" + ",".join(f"{v:.6f}" for v in emb) + "]"
                    cur.execute(
                        """UPDATE chunks
                           SET embedding = %s::vector,
                               embedding_model = %s,
                               embedding_version = %s,
                               embedded_at = now()
                           WHERE id = %s""",
                        (vec_str, MODEL_NAME, EMBEDDING_VERSION, str(chunk_id)),
                    )
                    updated += cur.rowcount
                except Exception as e:
                    log.warning("Error updating chunk %s: %s", chunk_id, e)
                    errors += 1

            conn.commit()
            if (i // BATCH_SIZE) % 10 == 0:
                elapsed = time.time() - start
                rate = updated / elapsed if elapsed > 0 else 0
                log.info("  %d/%d updated (%.0f/s)...", updated, len(chunk_ids), rate)

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info("Import complete:")
    log.info("  Updated: %d chunks", updated)
    log.info("  Errors: %d", errors)
    log.info("  Time: %.1f min", elapsed / 60)
    log.info("  Model: %s (%d dims)", MODEL_NAME, dims)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import BGE-M3 embeddings into chunks table")
    parser.add_argument("--embeddings", required=True, help="Path to embeddings_bge_m3.npy")
    parser.add_argument("--ids", required=True, help="Path to chunk_ids.npy")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    embeddings = np.load(args.embeddings)
    chunk_ids = np.load(args.ids, allow_pickle=True)
    log.info("Loaded %d embeddings (%s) and %d chunk IDs", len(embeddings), embeddings.shape, len(chunk_ids))

    conn = psycopg.connect(DATABASE_URL)
    try:
        import_embeddings(conn, embeddings, chunk_ids)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
