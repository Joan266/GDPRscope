"""
JurisMind — RAG Evaluation Pipeline

Métricas implementadas:
  Retrieval (sin LLM, rápido):
    - Hit Rate @K  — ¿aparece algún doc relevante en el top-K?
    - MRR          — Mean Reciprocal Rank del primer doc relevante
    - Context Precision @K — proporción de docs relevantes en el top-K

  Generation (con LLM, más lento, ~$0.15 para 25 preguntas):
    - Faithfulness       — ¿la respuesta está soportada por el contexto?
    - Answer Relevance   — ¿la respuesta es pertinente para la pregunta?

Uso:
    # Solo retrieval (rápido, sin LLM)
    python eval/run_eval.py --golden eval/golden_set.json

    # Retrieval + generación completa
    python eval/run_eval.py --golden eval/golden_set.json --llm

    # Guardar resultados
    python eval/run_eval.py --golden eval/golden_set.json --llm --out eval/results.json

    # Solo una categoría
    python eval/run_eval.py --golden eval/golden_set.json --category fine_lookup
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import psycopg

# Añadir raíz del proyecto a sys.path para importar db/rag.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from db import rag as rag_module

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
AWS_REGION   = os.environ.get("AWS_REGION", "us-east-1")
K_VALUES     = [1, 3, 5, 10]   # Hit Rate y Precision se calculan para cada K

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Judge prompts (Claude como evaluador) ──────────────────────────────────────

_FAITHFULNESS_PROMPT = """\
You are a strict GDPR legal expert evaluating whether an AI response is factually \
grounded in the provided source documents.

## Retrieved context:
{context}

## AI response to evaluate:
{response}

## Task:
Rate the faithfulness of the AI response on a scale from 0.0 to 1.0:
- 1.0 = Every factual claim in the response is directly supported by the context
- 0.5 = Most claims are supported, but some are inferred or missing from context
- 0.0 = Claims contradict the context or are entirely fabricated

Respond with ONLY a JSON object: {{"score": <float>, "reason": "<one sentence>"}}"""


_RELEVANCE_PROMPT = """\
You are evaluating whether an AI response answers the user's question.

## Question:
{question}

## AI response:
{response}

## Task:
Rate how well the response answers the question, from 0.0 to 1.0:
- 1.0 = Directly and completely answers the question with specific details
- 0.5 = Partially answers, missing key details or somewhat off-topic
- 0.0 = Does not answer the question or is entirely irrelevant

Respond with ONLY a JSON object: {{"score": <float>, "reason": "<one sentence>"}}"""


# ── Retrieval helpers ──────────────────────────────────────────────────────────

def retrieve_top_k(
    cur: psycopg.Cursor,
    bedrock_client,
    question: str,
    k: int = 10,
    filters: dict | None = None,
) -> tuple[list[dict], dict[str, float]]:
    """
    Corre hybrid search para `question` y devuelve (contexts, rrf_scores).
    contexts son los top-k padres tras RRF.
    Aplica intent extraction + pre-filtro + rerank cuando hay ANTHROPIC_API_KEY.
    """
    filters = dict(filters or {})  # copia para no mutar el dict del golden set
    intent = None

    if rag_module.ANTHROPIC_API_KEY:
        intent = rag_module.extract_intent(question)
        if intent:
            rag_module.apply_intent_filters(intent, filters)
            if intent.controller_name:
                doc_ids = rag_module._find_controller_docs(cur, intent.controller_name)
                if doc_ids:
                    filters["doc_ids"] = doc_ids

    # HyDE: for article_lookup queries, embed a hypothetical passage instead
    if intent and intent.gdpr_articles and rag_module.ANTHROPIC_API_KEY:
        try:
            query_vec = rag_module.hyde_embed(question, intent)
        except Exception:
            query_vec = rag_module.embed_query(bedrock_client, question)
    else:
        query_vec = rag_module.embed_query(bedrock_client, question)

    vector_hits = rag_module.search_vector_chunks(cur, query_vec, k * 2, filters)
    text_hits   = rag_module.search_text_chunks(cur, question, k * 2, filters)

    # Fine-sort injection: for sort_by=fine_desc, add a fine-ranked arm to RRF
    fine_hits: list[str] = []
    if intent and intent.sort_by == "fine_desc":
        fine_hits = rag_module._fetch_fine_sorted_chunks(cur, k * 2, filters)

    rrf_ranked  = rag_module.reciprocal_rank_fusion(vector_hits, text_hits, fine_hits or None)
    rrf_scores  = dict(rrf_ranked)
    top_child_ids = [cid for cid, _ in rrf_ranked[: k * 3]]

    contexts = rag_module.fetch_parent_context(cur, top_child_ids, rrf_scores, k)

    if intent and intent.sort_by:
        contexts = rag_module.rerank_by_metadata(contexts, intent)

    return contexts, rrf_scores


def doc_titles_from_contexts(contexts: list[dict]) -> list[str]:
    """Extrae los títulos únicos de los contextos recuperados (= source_ids para GDPRhub)."""
    seen: set[str] = set()
    titles: list[str] = []
    for ctx in contexts:
        t = ctx.get("title", "")
        if t and t not in seen:
            seen.add(t)
            titles.append(t)
    return titles


# ── Retrieval metrics ──────────────────────────────────────────────────────────

def hit_rate(retrieved_titles: list[str], relevant_ids: list[str], k: int) -> float:
    """1.0 si alguno de los top-k docs es relevante, 0.0 si no."""
    top_k = set(retrieved_titles[:k])
    return 1.0 if any(rid in top_k for rid in relevant_ids) else 0.0


def reciprocal_rank(retrieved_titles: list[str], relevant_ids: list[str]) -> float:
    """1/rank del primer doc relevante, 0.0 si ninguno."""
    relevant_set = set(relevant_ids)
    for rank, title in enumerate(retrieved_titles, start=1):
        if title in relevant_set:
            return 1.0 / rank
    return 0.0


def context_precision(retrieved_titles: list[str], relevant_ids: list[str], k: int) -> float:
    """Proporción de docs relevantes entre los top-k."""
    top_k = retrieved_titles[:k]
    if not top_k:
        return 0.0
    relevant_set = set(relevant_ids)
    hits = sum(1 for t in top_k if t in relevant_set)
    return hits / len(top_k)


# ── Generation metrics (LLM judge) ────────────────────────────────────────────

def _call_judge(bedrock_client, prompt: str) -> tuple[float, str]:
    """Llama a Claude via Anthropic API para evaluar — devuelve (score, reason)."""
    import re
    ac = rag_module._get_anthropic_client()
    msg = ac.messages.create(
        model=rag_module.MODEL_ID_LLM,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        return float(parsed["score"]), str(parsed.get("reason", ""))
    except Exception:
        m = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        score = float(m.group(1)) if m else 0.0
        return score, raw[:100]


def faithfulness_score(
    bedrock_client, contexts: list[dict], response: str
) -> tuple[float, str]:
    context_text = "\n\n".join(
        f"[{i+1}] {ctx.get('title','')}: {(ctx.get('content') or '')[:500]}"
        for i, ctx in enumerate(contexts[:5])
    )
    prompt = _FAITHFULNESS_PROMPT.format(context=context_text, response=response[:1500])
    return _call_judge(bedrock_client, prompt)


def answer_relevance_score(
    bedrock_client, question: str, response: str
) -> tuple[float, str]:
    prompt = _RELEVANCE_PROMPT.format(question=question, response=response[:1500])
    return _call_judge(bedrock_client, prompt)


# ── Evaluator ─────────────────────────────────────────────────────────────────

def evaluate(
    golden_set: list[dict],
    conn: psycopg.Connection,
    bedrock_client,
    run_llm: bool,
    category_filter: str | None,
) -> list[dict]:
    """Evalúa cada pregunta del golden set y devuelve lista de resultados."""

    if category_filter:
        golden_set = [q for q in golden_set if q.get("category") == category_filter]
        log.info("Filtro de categoría '%s': %d preguntas", category_filter, len(golden_set))

    results: list[dict] = []

    for i, item in enumerate(golden_set, start=1):
        qid      = item["id"]
        question = item["question"]
        relevant = item["relevant_source_ids"]
        filters  = item.get("filters", {})

        log.info("[%d/%d] %s — %s", i, len(golden_set), qid, question[:80])

        t0 = time.monotonic()
        try:
            with conn.cursor() as cur:
                contexts, rrf_scores = retrieve_top_k(
                    cur, bedrock_client, question, k=max(K_VALUES), filters=filters
                )
        except Exception as e:
            log.error("  Error en retrieval: %s", e)
            results.append({"id": qid, "error": str(e)})
            continue

        retrieved_titles = doc_titles_from_contexts(contexts)
        retrieval_ms = int((time.monotonic() - t0) * 1000)

        # Retrieval metrics
        hr  = {k: hit_rate(retrieved_titles, relevant, k) for k in K_VALUES}
        rr  = reciprocal_rank(retrieved_titles, relevant)
        cp  = {k: context_precision(retrieved_titles, relevant, k) for k in K_VALUES}

        result: dict = {
            "id":              qid,
            "category":        item.get("category", ""),
            "question":        question,
            "relevant_ids":    relevant,
            "retrieved_ids":   retrieved_titles,
            "retrieval_ms":    retrieval_ms,
            "hit_rate":        hr,
            "mrr":             rr,
            "context_precision": cp,
        }

        log.info(
            "  HR@5=%.2f  MRR=%.3f  retrieved=%s",
            hr.get(5, 0), rr,
            [t[:40] for t in retrieved_titles[:3]],
        )

        if run_llm:
            # Generar respuesta
            try:
                system_p, user_p = rag_module.build_prompt(question, contexts, [])
                response = rag_module.call_llm(bedrock_client, system_p, user_p)
            except Exception as e:
                log.error("  Error en LLM: %s", e)
                result["llm_error"] = str(e)
                results.append(result)
                continue

            # Judge metrics
            faith_score, faith_reason = faithfulness_score(bedrock_client, contexts, response)
            rel_score,   rel_reason   = answer_relevance_score(bedrock_client, question, response)

            result["response"]           = response[:2000]
            result["faithfulness"]       = faith_score
            result["faithfulness_reason"] = faith_reason
            result["answer_relevance"]   = rel_score
            result["answer_relevance_reason"] = rel_reason

            log.info("  Faithfulness=%.2f  Answer relevance=%.2f", faith_score, rel_score)

        results.append(result)

    return results


# ── Aggregate report ───────────────────────────────────────────────────────────

def print_report(results: list[dict]) -> None:
    valid = [r for r in results if "error" not in r]
    n = len(valid)
    if n == 0:
        print("No hay resultados válidos.")
        return

    print(f"\n{'=' * 65}")
    print(f"JurisMind RAG Evaluation — {n} preguntas evaluadas")
    print("=" * 65)

    # Retrieval metrics
    print("\n── Retrieval Metrics ──────────────────────────────────────")
    for k in K_VALUES:
        avg_hr = sum(r["hit_rate"].get(k, 0) for r in valid) / n
        avg_cp = sum(r["context_precision"].get(k, 0) for r in valid) / n
        print(f"  Hit Rate @{k:2d}        : {avg_hr:.3f}  ({avg_hr*100:.1f}%)")
        print(f"  Context Precision @{k:2d}: {avg_cp:.3f}  ({avg_cp*100:.1f}%)")

    avg_mrr = sum(r["mrr"] for r in valid) / n
    print(f"\n  MRR                 : {avg_mrr:.3f}")

    # Generation metrics (if present)
    llm_results = [r for r in valid if "faithfulness" in r]
    if llm_results:
        n_llm = len(llm_results)
        avg_faith = sum(r["faithfulness"] for r in llm_results) / n_llm
        avg_rel   = sum(r["answer_relevance"] for r in llm_results) / n_llm
        print(f"\n── Generation Metrics ({n_llm} questions) ─────────────────────")
        print(f"  Faithfulness        : {avg_faith:.3f}  ({avg_faith*100:.1f}%)")
        print(f"  Answer Relevance    : {avg_rel:.3f}  ({avg_rel*100:.1f}%)")

    # Per-category breakdown
    categories = sorted({r.get("category", "") for r in valid})
    if len(categories) > 1:
        print("\n── By Category ────────────────────────────────────────────")
        for cat in categories:
            cat_results = [r for r in valid if r.get("category") == cat]
            n_cat = len(cat_results)
            hr5 = sum(r["hit_rate"].get(5, 0) for r in cat_results) / n_cat
            mrr = sum(r["mrr"] for r in cat_results) / n_cat
            print(f"  {cat:<22} n={n_cat}  HR@5={hr5:.2f}  MRR={mrr:.3f}")

    # Failures
    failed = [r for r in valid if r["hit_rate"].get(5, 0) == 0]
    if failed:
        print(f"\n── Retrieval misses (HR@5=0): {len(failed)} preguntas ──────────────")
        for r in failed:
            print(f"  [{r['id']}] {r['question'][:70]}")

    print(f"\n{'=' * 65}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind RAG Evaluation")
    parser.add_argument("--golden", required=True, help="Path al golden set JSON")
    parser.add_argument("--llm",    action="store_true",
                        help="Evaluar también calidad de respuestas (lento, usa Bedrock LLM)")
    parser.add_argument("--out",    default=None, help="Guardar resultados en JSON")
    parser.add_argument("--category", default=None,
                        help="Evaluar solo una categoría del golden set")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL no definida.")

    golden_path = Path(args.golden)
    if not golden_path.exists():
        sys.exit(f"ERROR: Golden set no encontrado: {golden_path}")

    with open(golden_path, encoding="utf-8") as f:
        golden_set = json.load(f)

    log.info("Golden set cargado: %d preguntas", len(golden_set))

    log.info("Conectando a CockroachDB...")
    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    bedrock_client = rag_module.make_bedrock_client()

    results = evaluate(golden_set, conn, bedrock_client, args.llm, args.category)
    conn.close()

    print_report(results)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        log.info("Resultados guardados en %s", out_path)


if __name__ == "__main__":
    main()
