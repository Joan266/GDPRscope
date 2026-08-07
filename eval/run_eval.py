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

# Precios Anthropic (USD/token) — para estimación de coste del eval
_SONNET_PRICE_IN  = 3e-6   # $3/M input tokens
_SONNET_PRICE_OUT = 15e-6  # $15/M output tokens

# Acumulador de tokens para el eval completo (actualizado en _call_judge)
_token_counter: dict[str, int] = {"input": 0, "output": 0, "calls": 0}

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Judge prompts (Claude como evaluador) ──────────────────────────────────────

_FAITHFULNESS_PROMPT = """\
You are a strict GDPR legal expert. Compare an AI response against the retrieved \
source documents AND a known-correct reference answer.

## Retrieved context (what the AI had access to):
{context}

## Known-correct reference answer:
{ground_truth}

## AI response to evaluate:
{response}

## Task:
Rate faithfulness from 0.0 to 1.0 by checking two things:
1. Are the AI's factual claims supported by the retrieved context?
2. Do the AI's key facts (fine amounts, case numbers, articles cited) match the reference answer?

- 1.0 = All claims grounded in context AND consistent with reference answer
- 0.5 = Most claims correct but some facts missing, imprecise, or not in context
- 0.0 = Claims contradict context or reference answer, or are fabricated

Respond with ONLY a JSON object: {{"score": <float>, "reason": "<one sentence>"}}"""


_RELEVANCE_PROMPT = """\
You are evaluating whether an AI legal research response correctly answers the question, \
using a known-correct reference answer as the benchmark.

## Question:
{question}

## Known-correct reference answer:
{ground_truth}

## AI response to evaluate:
{response}

## Task:
Rate from 0.0 to 1.0 how well the AI response covers the key points in the reference answer:
- 1.0 = Covers all key facts from the reference (case ID, fine amount, articles, decision outcome)
- 0.5 = Covers the main conclusion but misses specific details (exact fine, articles, case number)
- 0.0 = Misses the point, gives wrong answer, or ignores the reference facts entirely

Respond with ONLY a JSON object: {{"score": <float>, "reason": "<one sentence>"}}"""


_LEGAL_PRECISION_PROMPT = """\
You are a GDPR legal expert comparing an AI response against a known-correct reference answer \
to evaluate accuracy of legal terminology and specific legal citations.

## Known-correct reference answer:
{ground_truth}

## AI response to evaluate:
{response}

## Task:
Rate legal precision from 0.0 to 1.0:

1.0 — AI uses the same specific articles (with sub-paragraphs), correct fine amounts, \
correct case numbers, and proper GDPR roles (controller/processor/data subject) as the \
reference answer.

0.5 — AI identifies the right area of law but lacks precision: mentions Art. 32 but not \
Art. 32(1)(b); gives approximate fine ("around €300K" vs exact €300,000); omits case number; \
uses generic terms ("privacy violation") instead of GDPR-specific language.

0.0 — AI cites wrong articles, wrong fine amounts, wrong case number, or uses non-GDPR \
terminology. Contradicts the reference answer on specific legal facts.

If the AI legitimately states it cannot answer (insufficient context), score 0.5.

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

    # HyDE: for article_lookup queries, embed a hypothetical passage
    if intent and intent.gdpr_articles and rag_module.ANTHROPIC_API_KEY:
        try:
            query_vec = rag_module.hyde_embed(question, intent)
        except Exception:
            query_vec = rag_module.embed_query(bedrock_client, question)
    else:
        query_vec = rag_module.embed_query(bedrock_client, question)

    vector_hits = rag_module.search_vector_chunks(cur, query_vec, k * 2, filters)
    text_hits   = rag_module.search_text_chunks(cur, question, k * 2, filters)

    # Fine-sort injection
    fine_hits: list[str] = []
    if intent and intent.sort_by == "fine_desc":
        fine_hits = rag_module._fetch_fine_sorted_chunks(cur, k * 2, filters)

    # HyPE question arm
    question_hits = rag_module.search_question_chunks(cur, query_vec, k * 2, filters)

    # Headnote arm — only for conceptual queries (no controller, no sort_by)
    headnote_hits: list[str] = []
    is_conceptual = not (intent and (intent.controller_name or intent.sort_by))
    if is_conceptual:
        headnote_hits = rag_module.search_headnote_chunks(cur, query_vec, k * 2, filters)

    # Case-number direct lookup — pin at top with score 1.0
    case_hits = rag_module._fetch_chunks_for_case_numbers(cur, question)

    # Query expansion for conceptual/scenario queries
    expansion_arms: list[list[str]] = []
    if is_conceptual and rag_module.ANTHROPIC_API_KEY:
        variants = rag_module.expand_query(question)
        for variant in variants:
            v_vec = rag_module.embed_query(bedrock_client, variant)
            v_hits = rag_module.search_vector_chunks(cur, v_vec, k * 2, filters)
            if v_hits:
                expansion_arms.append(v_hits)

    rrf_ranked  = rag_module.reciprocal_rank_fusion(
        vector_hits, text_hits, fine_hits or None,
        question_hits or None, headnote_hits or None,
        *(arm for arm in expansion_arms),
    )
    rrf_scores  = dict(rrf_ranked)
    if case_hits:
        for cid in case_hits:
            rrf_scores[cid] = max(rrf_scores.get(cid, 0.0), 1.0)
    top_child_ids = case_hits + [cid for cid, _ in rrf_ranked[: k * 3]
                                 if cid not in set(case_hits)]

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
    _token_counter["input"]  += msg.usage.input_tokens
    _token_counter["output"] += msg.usage.output_tokens
    _token_counter["calls"]  += 1
    raw = msg.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        return float(parsed["score"]), str(parsed.get("reason", ""))
    except Exception:
        m = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        score = float(m.group(1)) if m else 0.0
        return score, raw[:100]


def faithfulness_score(
    bedrock_client, contexts: list[dict], response: str, ground_truth: str = ""
) -> tuple[float, str]:
    """Judge compares response against retrieved context AND known-correct ground truth."""
    def _ctx_block(i: int, ctx: dict) -> str:
        fine = ctx.get("fine_amount")
        fine_str = f"Fine: {ctx.get('fine_currency','EUR')} {fine:,}" if fine else ""
        arts = ctx.get("gdpr_articles") or []
        arts_str = f"Articles: {', '.join(arts)}" if arts else ""
        meta = "  ".join(filter(None, [fine_str, arts_str]))
        return (
            f"[{i+1}] {ctx.get('title','')} | {ctx.get('authority','')} | {ctx.get('decision_year','')}\n"
            + (meta + "\n" if meta else "")
            + (ctx.get("content") or "")[:800]
        )

    context_text = "\n\n".join(_ctx_block(i, ctx) for i, ctx in enumerate(contexts[:8]))
    prompt = _FAITHFULNESS_PROMPT.format(
        context=context_text,
        ground_truth=ground_truth or "(not provided)",
        response=response[:2000],
    )
    return _call_judge(bedrock_client, prompt)


def answer_relevance_score(
    bedrock_client, question: str, response: str, ground_truth: str = ""
) -> tuple[float, str]:
    prompt = _RELEVANCE_PROMPT.format(
        question=question,
        ground_truth=ground_truth or "(not provided)",
        response=response[:1500],
    )
    return _call_judge(bedrock_client, prompt)


def legal_precision_score(
    bedrock_client, response: str, ground_truth: str = ""
) -> tuple[float, str]:
    """Evalúa precisión legal comparando contra la respuesta de referencia conocida."""
    prompt = _LEGAL_PRECISION_PROMPT.format(
        ground_truth=ground_truth or "(not provided)",
        response=response[:1500],
    )
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
    eval_t0 = time.monotonic()

    for i, item in enumerate(golden_set, start=1):
        qid          = item["id"]
        question     = item["question"]
        relevant     = item["relevant_source_ids"]
        filters      = item.get("filters", {})
        ground_truth = item.get("ground_truth", "")

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
            llm_t0 = time.monotonic()
            try:
                system_p, user_p = rag_module.build_prompt(question, contexts, [])
                response = rag_module.call_llm(bedrock_client, system_p, user_p)
            except Exception as e:
                log.error("  Error en LLM: %s", e)
                result["llm_error"] = str(e)
                results.append(result)
                continue

            llm_ms = int((time.monotonic() - llm_t0) * 1000)

            # Judge metrics — ground_truth como referencia objetiva
            faith_score, faith_reason = faithfulness_score(bedrock_client, contexts, response, ground_truth)
            rel_score,   rel_reason   = answer_relevance_score(bedrock_client, question, response, ground_truth)
            legal_score, legal_reason = legal_precision_score(bedrock_client, response, ground_truth)

            result["response"]                = response[:2000]
            result["llm_ms"]                  = llm_ms
            result["faithfulness"]            = faith_score
            result["faithfulness_reason"]     = faith_reason
            result["answer_relevance"]        = rel_score
            result["answer_relevance_reason"] = rel_reason
            result["legal_precision"]         = legal_score
            result["legal_precision_reason"]  = legal_reason

            log.info("  LLM: %d ms  Faithfulness=%.2f  Answer relevance=%.2f  Legal precision=%.2f",
                     llm_ms, faith_score, rel_score, legal_score)

        results.append(result)

    total_eval_ms = int((time.monotonic() - eval_t0) * 1000)
    log.info("Eval completado en %.1f s", total_eval_ms / 1000)
    if _token_counter["calls"] > 0:
        cost = (_token_counter["input"] * _SONNET_PRICE_IN
                + _token_counter["output"] * _SONNET_PRICE_OUT)
        log.info("Judge tokens: %d in / %d out — coste estimado: $%.4f (%d llamadas)",
                 _token_counter["input"], _token_counter["output"], cost,
                 _token_counter["calls"])

    # Añadir resumen de coste/tiempo a los resultados para print_report
    results.append({
        "_meta": True,
        "total_eval_ms":   total_eval_ms,
        "judge_input_tok": _token_counter["input"],
        "judge_output_tok":_token_counter["output"],
        "judge_calls":     _token_counter["calls"],
    })
    return results


# ── Aggregate report ───────────────────────────────────────────────────────────

def print_report(results: list[dict]) -> None:
    meta  = next((r for r in results if r.get("_meta")), {})
    valid = [r for r in results if "error" not in r and not r.get("_meta")]
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
        avg_faith  = sum(r["faithfulness"] for r in llm_results) / n_llm
        avg_rel    = sum(r["answer_relevance"] for r in llm_results) / n_llm
        avg_legal  = sum(r.get("legal_precision", 0) for r in llm_results) / n_llm
        print(f"\n── Generation Metrics ({n_llm} questions) ─────────────────────")
        print(f"  Faithfulness        : {avg_faith:.3f}  ({avg_faith*100:.1f}%)")
        print(f"  Answer Relevance    : {avg_rel:.3f}  ({avg_rel*100:.1f}%)")
        print(f"  Legal Precision     : {avg_legal:.3f}  ({avg_legal*100:.1f}%)")

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

    # Timing
    avg_retrieval_ms = sum(r.get("retrieval_ms", 0) for r in valid) / n
    print(f"\n── Timing ─────────────────────────────────────────────────")
    print(f"  Retrieval latency avg : {avg_retrieval_ms:.0f} ms")
    llm_results = [r for r in valid if "llm_ms" in r]
    if llm_results:
        avg_llm_ms = sum(r["llm_ms"] for r in llm_results) / len(llm_results)
        print(f"  LLM generation avg    : {avg_llm_ms:.0f} ms")
    if meta.get("total_eval_ms"):
        print(f"  Total eval duration   : {meta['total_eval_ms'] / 1000:.1f} s")

    # Cost
    in_tok  = meta.get("judge_input_tok", 0)
    out_tok = meta.get("judge_output_tok", 0)
    calls   = meta.get("judge_calls", 0)
    if calls > 0:
        cost = in_tok * _SONNET_PRICE_IN + out_tok * _SONNET_PRICE_OUT
        print(f"\n── Cost (judge LLM only) ──────────────────────────────────")
        print(f"  Judge calls           : {calls}")
        print(f"  Tokens in / out       : {in_tok:,} / {out_tok:,}")
        print(f"  Estimated cost        : ${cost:.4f}")
        print(f"  Cost per question     : ${cost / n:.4f}")

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
            json.dump([r for r in results if not r.get("_meta")],
                      f, indent=2, ensure_ascii=False, default=str)
        log.info("Resultados guardados en %s", out_path)


if __name__ == "__main__":
    main()
