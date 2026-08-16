"""
Fast import of BGE-M3 embeddings using temp table + batch INSERT + JOIN UPDATE.

Much faster than row-by-row UPDATE (~5 min vs ~3 hours for 83K rows).

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python db/import_embeddings_fast.py
"""

import logging
import os
import sys
import time

import numpy as np
import psycopg
from psycopg.types.json import set_json_dumps

EMBEDDINGS_FILE = "eval/embeddings_bge_m3.npy"
IDS_FILE = "eval/chunk_ids_bge_m3.npy"
MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_VERSION = "bge-m3-1024"
BATCH_SIZE = 500
DATABASE_URL = os.environ.get("DATABASE_URL", "")

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    embeddings = np.load(EMBEDDINGS_FILE)
    chunk_ids = np.load(IDS_FILE, allow_pickle=True)
    log.info("Loaded %d embeddings (%s)", len(embeddings), embeddings.shape)

    conn = psycopg.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            # 1. Create temp table
            cur.execute("DROP TABLE IF EXISTS _tmp_embeddings")
            cur.execute("""
                CREATE TEMP TABLE _tmp_embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    embedding vector(1024)
                )
            """)
            conn.commit()
            log.info("Temp table created")

            # 2. Batch insert into temp table
            start = time.time()
            inserted = 0
            for i in range(0, len(chunk_ids), BATCH_SIZE):
                batch_ids = chunk_ids[i:i + BATCH_SIZE]
                batch_embs = embeddings[i:i + BATCH_SIZE]

                values = []
                for cid, emb in zip(batch_ids, batch_embs):
                    vec_str = "[" + ",".join(f"{v:.6f}" for v in emb) + "]"
                    values.append((str(cid), vec_str))

                cur.executemany(
                    "INSERT INTO _tmp_embeddings (chunk_id, embedding) VALUES (%s, %s::vector)",
                    values,
                )
                conn.commit()
                inserted += len(batch_ids)

                if (i // BATCH_SIZE) % 20 == 0:
                    elapsed = time.time() - start
                    rate = inserted / elapsed if elapsed > 0 else 0
                    log.info("  Inserted %d/%d into temp (%.0f/s)", inserted, len(chunk_ids), rate)

            elapsed = time.time() - start
            log.info("Temp table loaded: %d rows in %.1f min", inserted, elapsed / 60)

            # 3. Single UPDATE via JOIN
            log.info("Running batch UPDATE chunks via JOIN...")
            t0 = time.time()
            cur.execute("""
                UPDATE chunks c
                SET embedding = t.embedding,
                    embedding_model = %s,
                    embedding_version = %s,
                    embedded_at = now()
                FROM _tmp_embeddings t
                WHERE c.id = t.chunk_id::uuid
            """, (MODEL_NAME, EMBEDDING_VERSION))
            updated = cur.rowcount
            conn.commit()
            log.info("Updated %d chunks in %.1f s", updated, time.time() - t0)

            # 4. Cleanup
            cur.execute("DROP TABLE IF EXISTS _tmp_embeddings")
            conn.commit()

        total_elapsed = time.time() - start
        log.info("=" * 60)
        log.info("Import complete: %d chunks updated in %.1f min", updated, total_elapsed / 60)
        log.info("Model: %s (%d dims)", MODEL_NAME, embeddings.shape[1])

        if updated < len(chunk_ids):
            log.warning("MISMATCH: %d embeddings but only %d chunks matched in DB", len(chunk_ids), updated)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
