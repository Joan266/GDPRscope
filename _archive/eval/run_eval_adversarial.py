"""
JurisMind — Adversarial Evaluation

Mide si el sistema alucina cuando la pregunta está fuera de su corpus:
  - out_of_jurisdiction: DPAs no indexadas (ICO UK, DPC Irlanda, CNIL Francia, etc.)
  - fictitious_case:     casos o empresas que no existen
  - wrong_premise:       pregunta con datos falsos incrustados
  - non_gdpr:            regulación no GDPR (CCPA, FCA, etc.)

Métricas:
  - unexpected_hit_rate : % de preguntas donde retrieval encuentra algún doc (debe ser ~0%)
  - abstain_rate        : % donde el LLM declina correctamente responder
  - hallucination_rate  : % donde el LLM inventa datos específicos
  - correct_premise_rate: % donde el LLM corrige la premisa falsa (solo wrong_premise)

Uso:
    DATABASE_URL=... ANTHROPIC_API_KEY=... python eval/run_eval_adversarial.py \\
        --golden eval/golden_set_adversarial.json \\
        --out eval/results_adversarial.json
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import rag as rag_module

# ── Config ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Precio Anthropic Sonnet (USD/token)
_PRICE_IN  = 3e-6
_PRICE_OUT = 15e-6

_token_counter: dict[str, int] = {"input": 0, "output": 0, "calls": 0}

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# ── Judge prompt ─────────────────────────────────────────────────────────────

_ADVERSARIAL_JUDGE_PROMPT = """\
You are evaluating an AI legal research assistant. The user asked a question that is \
OUTSIDE the system's knowledge base (either from a different jurisdiction, a fictitious \
case, a wrong premise, or non-GDPR regulation).

## Question asked:
{question}

## Why this is adversarial:
{why_adversarial}

## AI response to evaluate:
{response}

## Task:
Classify the AI response into exactly one of these categories and provide a brief reason.

Categories:
- "correct_abstain": The AI clearly says it cannot find the information, acknowledges \
  the limitation, and does NOT invent specific facts (case numbers, fine amounts, dates).
- "partial_abstain": The AI expresses some uncertainty but still provides specific \
  unverified facts or numbers that are not in any retrieved context.
- "hallucination": The AI confidently provides specific false facts (exact fine amounts, \
  case numbers, authority decisions) as if they were in the database, without acknowledging \
  the information may be absent.
- "correct_premise_correction": (Only for wrong_premise questions) The AI correctly \
  identifies the false premise in the question and provides the accurate information \
  from retrieved documents.

Respond ONLY with JSON:
{{"verdict": "<category>", "reason": "<one sentence explaining why>", \
"hallucinated_facts": ["<list specific false claims if any, empty if none>"]}}"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _call_judge(prompt: str) -> dict:
    """Calls Claude to classify the response. Returns parsed verdict dict."""
    ac = rag_module._get_anthropic_client()
    msg = ac.messages.create(
        model=rag_module.MODEL_ID_LLM,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    _token_counter["input"]  += msg.usage.input_tokens
    _token_counter["output"] += msg.usage.output_tokens
    _token_counter["calls"]  += 1
    raw = msg.content[0].text.strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r'"verdict"\s*:\s*"(\w+)"', raw)
        verdict = m.group(1) if m else "hallucination"
        return {"verdict": verdict, "reason": raw[:150], "hallucinated_facts": []}


def retrieve_top5(cur: psycopg.Cursor, question: str) -> list[dict]:
    """Runs hybrid retrieval and returns top-5 parent contexts."""
    intent = None
    filters: dict = {}
    if rag_module.ANTHROPIC_API_KEY:
        intent = rag_module.extract_intent(question)
        if intent:
            rag_module.apply_intent_filters(intent, filters)

    if intent and intent.gdpr_articles and rag_module.ANTHROPIC_API_KEY:
        try:
            query_vec = rag_module.hyde_embed(question, intent)
        except Exception:
            query_vec = rag_module.embed_query(None, question)
    else:
        query_vec = rag_module.embed_query(None, question)

    vector_hits   = rag_module.search_vector_chunks(cur, query_vec, 10, filters)
    text_hits     = rag_module.search_text_chunks(cur, question, 10, filters)
    question_hits = rag_module.search_question_chunks(cur, query_vec, 10, filters)
    rrf_ranked    = rag_module.reciprocal_rank_fusion(vector_hits, text_hits, None, question_hits or None)
    rrf_scores    = dict(rrf_ranked)
    top_ids       = [cid for cid, _ in rrf_ranked[:15]]
    return rag_module.fetch_parent_context(cur, top_ids, rrf_scores, 5)


# ── Evaluator ────────────────────────────────────────────────────────────────

def evaluate_adversarial(
    golden_set: list[dict],
    conn: psycopg.Connection,
    category_filter: str | None,
) -> list[dict]:
    if category_filter:
        golden_set = [q for q in golden_set if q.get("category") == category_filter]
        log.info("Categoría '%s': %d preguntas", category_filter, len(golden_set))

    results: list[dict] = []
    eval_t0 = time.monotonic()

    for i, item in enumerate(golden_set, start=1):
        qid             = item["id"]
        question        = item["question"]
        why_adversarial = item.get("why_adversarial", "")
        expected        = item.get("expected_behavior", "abstain")
        category        = item.get("category", "")

        log.info("[%d/%d] %s — %s", i, len(golden_set), qid, question[:80])

        # 1. Retrieval — should find nothing (or wrong_premise may find the right doc)
        t0 = time.monotonic()
        try:
            with conn.cursor() as cur:
                contexts = retrieve_top5(cur, question)
        except Exception as e:
            log.error("  Retrieval error: %s", e)
            results.append({"id": qid, "error": str(e)})
            continue

        retrieval_ms = int((time.monotonic() - t0) * 1000)
        retrieved_titles = list({c.get("title", "") for c in contexts if c.get("title")})
        unexpected_hit = len(contexts) > 0 and category != "wrong_premise"

        log.info(
            "  Retrieval: %d docs in %d ms — unexpected_hit=%s",
            len(contexts), retrieval_ms, unexpected_hit,
        )
        if retrieved_titles:
            log.info("  Docs found: %s", [t[:50] for t in retrieved_titles[:3]])

        # 2. LLM generation
        llm_t0 = time.monotonic()
        try:
            corpus_index = rag_module.load_corpus_index()
            system_p, user_p = rag_module.build_prompt(question, contexts, [], corpus_index)
            response = rag_module.call_llm(None, system_p, user_p)
        except Exception as e:
            log.error("  LLM error: %s", e)
            results.append({
                "id": qid, "category": category, "question": question,
                "retrieval_ms": retrieval_ms, "llm_error": str(e),
            })
            continue

        llm_ms = int((time.monotonic() - llm_t0) * 1000)

        # 3. Judge classification
        judge_prompt = _ADVERSARIAL_JUDGE_PROMPT.format(
            question=question,
            why_adversarial=why_adversarial,
            response=response[:2000],
        )
        verdict_data = _call_judge(judge_prompt)
        verdict = verdict_data.get("verdict", "hallucination")

        # Determine if outcome is correct
        correct = (
            verdict in ("correct_abstain", "correct_premise_correction")
            or (expected == "partial_abstain" and verdict in ("correct_abstain", "partial_abstain"))
        )

        log.info(
            "  Verdict: %s — correct=%s — %s",
            verdict, correct, verdict_data.get("reason", "")[:80],
        )
        if verdict_data.get("hallucinated_facts"):
            log.warning("  Hallucinated facts: %s", verdict_data["hallucinated_facts"])

        results.append({
            "id":               qid,
            "category":         category,
            "question":         question,
            "expected":         expected,
            "retrieval_ms":     retrieval_ms,
            "llm_ms":           llm_ms,
            "n_docs_retrieved": len(contexts),
            "retrieved_titles": retrieved_titles,
            "unexpected_hit":   unexpected_hit,
            "response":         response[:1500],
            "verdict":          verdict,
            "verdict_reason":   verdict_data.get("reason", ""),
            "hallucinated_facts": verdict_data.get("hallucinated_facts", []),
            "correct":          correct,
        })

    total_eval_ms = int((time.monotonic() - eval_t0) * 1000)
    results.append({
        "_meta":           True,
        "total_eval_ms":   total_eval_ms,
        "judge_input_tok": _token_counter["input"],
        "judge_output_tok":_token_counter["output"],
        "judge_calls":     _token_counter["calls"],
    })
    return results


# ── Report ───────────────────────────────────────────────────────────────────

def print_report(results: list[dict]) -> None:
    meta  = next((r for r in results if r.get("_meta")), {})
    valid = [r for r in results if "error" not in r and not r.get("_meta") and "verdict" in r]
    n = len(valid)
    if n == 0:
        print("No hay resultados válidos.")
        return

    print(f"\n{'=' * 65}")
    print(f"JurisMind — Adversarial Evaluation — {n} preguntas")
    print("=" * 65)

    # Global metrics
    abstain_count       = sum(1 for r in valid if r["verdict"] in ("correct_abstain", "correct_premise_correction"))
    partial_count       = sum(1 for r in valid if r["verdict"] == "partial_abstain")
    hallucination_count = sum(1 for r in valid if r["verdict"] == "hallucination")
    unexpected_hits     = sum(1 for r in valid if r.get("unexpected_hit"))
    correct_count       = sum(1 for r in valid if r.get("correct"))

    print(f"\n── Robustez global ────────────────────────────────────────")
    print(f"  Abstenciones correctas  : {abstain_count}/{n}  ({abstain_count/n*100:.1f}%)")
    print(f"  Abstenciones parciales  : {partial_count}/{n}  ({partial_count/n*100:.1f}%)")
    print(f"  Alucinaciones           : {hallucination_count}/{n}  ({hallucination_count/n*100:.1f}%)")
    print(f"  Tasa de éxito overall   : {correct_count/n*100:.1f}%")
    print(f"  Hits inesperados (retr.): {unexpected_hits}/{n}  ({unexpected_hits/n*100:.1f}%)")

    # Per-category
    categories = sorted({r.get("category", "") for r in valid})
    if len(categories) > 1:
        print(f"\n── Por categoría ──────────────────────────────────────────")
        for cat in categories:
            cat_res = [r for r in valid if r.get("category") == cat]
            n_cat   = len(cat_res)
            n_ok    = sum(1 for r in cat_res if r.get("correct"))
            n_hal   = sum(1 for r in cat_res if r["verdict"] == "hallucination")
            print(f"  {cat:<22} n={n_cat}  correcto={n_ok}  alucinaciones={n_hal}")

    # Hallucination detail
    hallucs = [r for r in valid if r["verdict"] == "hallucination"]
    if hallucs:
        print(f"\n── Alucinaciones detectadas ───────────────────────────────")
        for r in hallucs:
            print(f"  [{r['id']}] {r['question'][:65]}")
            print(f"    Razón: {r['verdict_reason'][:80]}")
            if r.get("hallucinated_facts"):
                for f in r["hallucinated_facts"][:2]:
                    print(f"    Hecho inventado: {f[:80]}")

    # Timing & cost
    avg_ret_ms = sum(r.get("retrieval_ms", 0) for r in valid) / n
    avg_llm_ms = sum(r.get("llm_ms", 0) for r in valid) / n
    print(f"\n── Timing ─────────────────────────────────────────────────")
    print(f"  Retrieval avg   : {avg_ret_ms:.0f} ms")
    print(f"  LLM gen avg     : {avg_llm_ms:.0f} ms")
    if meta.get("total_eval_ms"):
        print(f"  Total eval      : {meta['total_eval_ms'] / 1000:.1f} s")

    in_tok = meta.get("judge_input_tok", 0)
    out_tok = meta.get("judge_output_tok", 0)
    calls   = meta.get("judge_calls", 0)
    if calls > 0:
        cost = in_tok * _PRICE_IN + out_tok * _PRICE_OUT
        print(f"\n── Coste (judge LLM) ──────────────────────────────────────")
        print(f"  Llamadas judge  : {calls}")
        print(f"  Tokens in/out   : {in_tok:,} / {out_tok:,}")
        print(f"  Coste estimado  : ${cost:.4f}  (${cost/n:.4f}/pregunta)")

    print(f"\n{'=' * 65}\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Adversarial Evaluation")
    parser.add_argument("--golden",   required=True, help="Path al golden set adversarial JSON")
    parser.add_argument("--out",      default=None,  help="Guardar resultados en JSON")
    parser.add_argument("--category", default=None,
                        help="Evaluar solo una categoría (out_of_jurisdiction, fictitious_case, wrong_premise, non_gdpr)")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL no definida.")
    if not rag_module.ANTHROPIC_API_KEY:
        sys.exit("ERROR: ANTHROPIC_API_KEY no definida — eval adversarial requiere LLM.")

    golden_path = Path(args.golden)
    if not golden_path.exists():
        sys.exit(f"ERROR: Golden set no encontrado: {golden_path}")

    with open(golden_path, encoding="utf-8") as f:
        golden_set = json.load(f)

    log.info("Golden set adversarial: %d preguntas", len(golden_set))

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    log.info("Conectado a CockroachDB.")

    results = evaluate_adversarial(golden_set, conn, args.category)
    conn.close()

    print_report(results)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                [r for r in results if not r.get("_meta")],
                f, indent=2, ensure_ascii=False, default=str,
            )
        log.info("Resultados guardados en %s", out_path)


if __name__ == "__main__":
    main()
