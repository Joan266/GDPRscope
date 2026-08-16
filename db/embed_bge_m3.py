"""
Generate BGE-M3 embeddings locally on GPU.

Reads chunks_to_embed.csv.gz, generates embeddings with BGE-M3,
saves incrementally to avoid data loss.

Usage:
    PYTHONUTF8=1 python db/embed_bge_m3.py
"""

import logging
import time

import numpy as np
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer

BATCH_SIZE = 8
SAVE_EVERY = 2000
SLEEP_BETWEEN_BATCHES = 0.5  # seconds — throttle GPU to ~20%
PART = 4  # Change to 3 or 4
INPUT_FILE = f"eval/chunks_part{PART}.csv.gz"
OUTPUT_EMBEDDINGS = f"eval/embeddings_part{PART}.npy"
OUTPUT_IDS = f"eval/chunk_ids_part{PART}.npy"

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s", device)
    if device == "cuda":
        log.info("GPU: %s", torch.cuda.get_device_name(0))
        log.info("VRAM: %.1f GB", torch.cuda.get_device_properties(0).total_memory / 1e9)

    log.info("Loading data from %s", INPUT_FILE)
    df = pd.read_csv(INPUT_FILE, compression="gzip")
    log.info("Loaded %d chunks", len(df))
    log.info("Sections: %s", df["section"].value_counts().to_dict())

    log.info("Loading BGE-M3 model...")
    model = SentenceTransformer("BAAI/bge-m3", device=device)
    log.info("Model loaded: %d dims", model.get_embedding_dimension())

    texts = df["content"].tolist()
    chunk_ids = df["chunk_id"].values
    all_embeddings: list[np.ndarray] = []
    start = time.time()
    last_save = 0

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embs = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        all_embeddings.append(embs)
        done = i + len(batch)
        time.sleep(SLEEP_BETWEEN_BATCHES)

        if done - last_save >= SAVE_EVERY or done == len(texts):
            partial = np.vstack(all_embeddings)
            np.save(OUTPUT_EMBEDDINGS, partial)
            np.save(OUTPUT_IDS, chunk_ids[:done])
            last_save = done
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(texts) - done) / rate if rate > 0 else 0
            log.info(
                "SAVED %d/%d (%.1f%%) — %.0f chunks/s — ETA %.1f min",
                done, len(texts), 100 * done / len(texts), rate, eta / 60,
            )

    elapsed = time.time() - start
    final = np.vstack(all_embeddings)
    np.save(OUTPUT_EMBEDDINGS, final)
    np.save(OUTPUT_IDS, chunk_ids)
    log.info("=" * 60)
    log.info("Done: %d embeddings (%s) in %.1f min", len(final), final.shape, elapsed / 60)
    log.info("Saved: %s + %s", OUTPUT_EMBEDDINGS, OUTPUT_IDS)


if __name__ == "__main__":
    main()
