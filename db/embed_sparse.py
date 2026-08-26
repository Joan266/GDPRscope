"""
Generate BGE-M3 sparse (lexical) embeddings for all chunks.

Uses the raw transformers approach (sparse_linear.pt head) to avoid
FlagEmbedding compatibility issues with transformers 4.52+.

Stores sparse vectors as JSONB in chunks.sparse_embedding column.
Format: {"token_id": weight, ...} — typically 50-100 non-zero tokens per chunk.

Usage:
    PYTHONUTF8=1 python db/embed_sparse.py
    PYTHONUTF8=1 python db/embed_sparse.py --batch-size 16
"""

import argparse
import glob
import json
import logging
import os
import time

import psycopg
import torch
from transformers import AutoModel, AutoTokenizer

DATABASE_URL = os.environ["DATABASE_URL"]
MIN_WEIGHT = 0.01  # discard tokens with weight below this
SLEEP_BETWEEN_BATCHES = 0.3  # seconds — gentle GPU throttle

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _load_sparse_head(device: str) -> tuple[torch.Tensor, torch.Tensor]:
    """Load the sparse linear head from HuggingFace cache."""
    cache = os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-m3")
    matches = glob.glob(os.path.join(cache, "**", "sparse_linear.pt"), recursive=True)
    if not matches:
        raise FileNotFoundError(
            "sparse_linear.pt not found. Run: "
            "python -c \"from transformers import AutoModel; AutoModel.from_pretrained('BAAI/bge-m3')\""
        )
    sp = torch.load(matches[0], map_location=device, weights_only=True)
    return sp["weight"], sp["bias"]  # [1, 1024], [1]


def encode_sparse_batch(
    model: AutoModel,
    tokenizer: AutoTokenizer,
    sp_w: torch.Tensor,
    sp_b: torch.Tensor,
    texts: list[str],
    device: str,
) -> list[dict[str, float]]:
    """Encode a batch of texts into sparse vectors."""
    encoded = tokenizer(
        texts, padding=True, truncation=True,
        return_tensors="pt", max_length=8192,
    ).to(device)

    with torch.no_grad():
        hidden = model(**encoded).last_hidden_state  # [batch, seq, 1024]
        weights = torch.relu(hidden @ sp_w.T + sp_b).squeeze(-1)  # [batch, seq]

    results = []
    for i in range(len(texts)):
        input_ids = encoded["input_ids"][i].tolist()
        token_weights = weights[i].tolist()
        sparse: dict[str, float] = {}
        for tid, w in zip(input_ids, token_weights):
            if w > MIN_WEIGHT and tid not in (0, 1, 2):  # skip pad/bos/eos
                key = str(tid)
                sparse[key] = round(max(sparse.get(key, 0.0), w), 4)
        results.append(sparse)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--commit-every", type=int, default=200)
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s", device)
    if device == "cuda":
        log.info("GPU: %s (%.1f GB)", torch.cuda.get_device_name(0),
                 torch.cuda.get_device_properties(0).total_memory / 1e9)

    log.info("Loading BGE-M3 model + sparse head...")
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3").half().to(device)
    model.eval()
    sp_w, sp_b = _load_sparse_head(device)
    log.info("Model loaded. VRAM: %.2f GB", torch.cuda.memory_allocated() / 1e9 if device == "cuda" else 0)

    with psycopg.connect(DATABASE_URL) as conn:
        # Ensure column exists
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'chunks' AND column_name = 'sparse_embedding'
            """)
            if not cur.fetchone():
                log.info("Adding sparse_embedding column...")
                cur.execute("ALTER TABLE chunks ADD COLUMN sparse_embedding JSONB")
                conn.commit()

        # Count pending
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM chunks
                WHERE embedding_version = 'bge-m3-1024'
                  AND sparse_embedding IS NULL
            """)
            total = cur.fetchone()[0]
            log.info("Chunks needing sparse: %d", total)

        if total == 0:
            log.info("Nothing to do")
            return

        processed = 0
        start = time.time()

        while True:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, content FROM chunks
                    WHERE embedding_version = 'bge-m3-1024'
                      AND sparse_embedding IS NULL
                    LIMIT %s
                """, (args.batch_size * args.commit_every,))
                batch_rows = cur.fetchall()

            if not batch_rows:
                break

            # Process in sub-batches for GPU
            for i in range(0, len(batch_rows), args.batch_size):
                sub = batch_rows[i:i + args.batch_size]
                ids = [str(r[0]) for r in sub]
                texts = [r[1] for r in sub]

                sparse_vecs = encode_sparse_batch(model, tokenizer, sp_w, sp_b, texts, device)
                time.sleep(SLEEP_BETWEEN_BATCHES)

                with conn.cursor() as cur:
                    for chunk_id, sparse in zip(ids, sparse_vecs):
                        cur.execute(
                            "UPDATE chunks SET sparse_embedding = %s WHERE id = %s",
                            (json.dumps(sparse), chunk_id),
                        )

                processed += len(sub)

                if processed % (args.batch_size * 10) == 0 or processed >= total:
                    conn.commit()
                    elapsed = time.time() - start
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (total - processed) / rate if rate > 0 else 0
                    log.info(
                        "%d/%d (%.1f%%) — %.1f chunks/s — ETA %.1f min",
                        processed, total, 100 * processed / total, rate, eta / 60,
                    )

            conn.commit()

    elapsed = time.time() - start
    log.info("Done: %d chunks in %.1f min (%.1f chunks/s)", processed, elapsed / 60, processed / elapsed)


if __name__ == "__main__":
    main()
