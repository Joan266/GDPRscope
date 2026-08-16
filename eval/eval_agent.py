"""
GDPRScope — Agent Evaluation Pipeline

Compares agentic multi-turn retrieval against single-query RAG baseline.
Runs each golden set query through the LangGraph agent, extracts all
document titles from tool results, and measures HR@K / MRR.

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python eval/eval_agent.py --golden eval/golden_set_v4.json --output eval/results_agent_v1.json

    # Limit to N queries (for testing)
    PYTHONUTF8=1 python eval/eval_agent.py --golden eval/golden_set_v4.json --limit 10
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
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool as lc_tool

sys.path.insert(0, str(Path(__file__).parent.parent))
from services.agent import create_tools, run_agent

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
K_VALUES = [1, 3, 5, 10]

# Pattern to extract document titles from agent tool results.
# Tools format: "1. **TITLE**\n   DPA: ..."
_TITLE_PATTERN = re.compile(r"\d+\.\s+\*\*(.+?)\*\*")

# --- Fix 1: Resolve numeric enforcement_tracker IDs to DB titles ---

def load_tracker_id_map(tracker_path: str = "data/tracker_full.json") -> dict[str, str]:
    """Build mapping: numeric tracker ID → full title as stored in DB.

    tracker_full.json uses key 'e' for the numeric ID, 'a' for authority,
    'p' for party, and 'C' for country.  DB titles follow the format:
    'Authority — Party (Country) [ET-ID, ...]'
    """
    path = Path(tracker_path)
    if not path.exists():
        log.warning("tracker_full.json not found at %s — numeric IDs won't resolve", path)
        return {}

    with open(path, encoding="utf-8") as f:
        tracker = json.load(f)

    id_map: dict[str, str] = {}
    for entry in tracker:
        eid = str(entry.get("e", ""))
        auth = entry.get("a", "")
        party = entry.get("p", "")
        country = entry.get("C", "")
        if eid and auth and party:
            # Reconstruct the title as stored in DB by ingest.py
            title = f"{auth} — {party} ({country})"
            id_map[eid] = title
    log.info("Loaded %d tracker ID → title mappings", len(id_map))
    return id_map


def resolve_relevant_ids(
    relevant_ids: list[str],
    tracker_map: dict[str, str],
) -> list[str]:
    """Replace numeric IDs with their tracker titles for matching."""
    resolved: list[str] = []
    for rid in relevant_ids:
        if rid.isdigit() and rid in tracker_map:
            resolved.append(tracker_map[rid])
        else:
            resolved.append(rid)
    return resolved


def extract_titles_from_messages(messages: list) -> list[str]:
    """Extract unique document titles from all agent messages (tool results)."""
    seen: set[str] = set()
    titles: list[str] = []

    for msg in messages:
        content = ""
        if hasattr(msg, "content"):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if not content:
            continue

        for m in _TITLE_PATTERN.finditer(content):
            title = m.group(1).strip()
            if title and title not in seen:
                seen.add(title)
                titles.append(title)

    return titles


def hit_rate(retrieved: list[str], relevant: list[str], k: int) -> float:
    """1.0 if any relevant doc appears in top-k retrieved, else 0.0."""
    top_k = set(retrieved[:k])
    return 1.0 if any(rid in top_k for rid in relevant) else 0.0


def reciprocal_rank(retrieved: list[str], relevant: list[str]) -> float:
    """1/rank of first relevant doc, 0.0 if not found."""
    relevant_set = set(relevant)
    for rank, title in enumerate(retrieved, start=1):
        if title in relevant_set:
            return 1.0 / rank
    return 0.0


def _normalize_dpa(text: str) -> str:
    """Normalize DPA authority names for comparison."""
    t = text.lower()
    # Strip common prefixes/suffixes
    for prefix in ("the ", "national "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t


def _extract_case_numbers(text: str) -> set[str]:
    """Extract case/reference numbers like SAN-2021-020, C-311/18, APD/GBA 81/2020."""
    patterns = [
        r'[A-Z]{2,}-\d{4}-\d+',          # SAN-2021-020 (full, match first)
        r'C-\d+/\d+',                     # CJEU: C-311/18
        r'\d+/\d{4}',                     # 81/2020
        r'ET-\d+',                        # Enforcement Tracker ID
        r'EXP\d{8,}',                     # EXP202210347
        r'PS/\d{5}/\d{4}',               # PS/00268/2020
    ]
    nums = set()
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            nums.add(m.group(0).upper())
    return nums


def _extract_controller(text: str) -> str | None:
    """Extract controller/company name from title formats."""
    # "Authority — COMPANY (Country)" format (tracker)
    m = re.search(r'—\s*(.+?)\s*\(', text)
    if m:
        name = m.group(1).strip()
        if name.lower() not in ("unknown",):
            return name
    # "Authority - CASE_NUMBER" format — no controller
    return None


def fuzzy_match(retrieved: list[str], relevant: list[str]) -> list[str]:
    """Fuzzy matching with case number, controller name, and DPA normalization."""
    matched: list[str] = []
    for rid in relevant:
        rid_lower = rid.lower()
        rid_case_nums = _extract_case_numbers(rid)
        rid_controller = _extract_controller(rid)

        for title in retrieved:
            title_lower = title.lower()

            # 1. Exact match
            if rid_lower == title_lower:
                if title not in matched:
                    matched.append(title)
                break

            # 2. Substring: relevant_id contained in title or vice versa
            if rid_lower in title_lower or title_lower in rid_lower:
                if title not in matched:
                    matched.append(title)
                break

            # 3. Case number overlap (SAN-2021-020, C-311/18, etc.)
            title_case_nums = _extract_case_numbers(title)
            if rid_case_nums and title_case_nums and rid_case_nums & title_case_nums:
                if title not in matched:
                    matched.append(title)
                break

            # 4. Controller name match (e.g. relevant has "SLIMPAY",
            #    retrieved has "... SLIMPAY ...")
            if rid_controller and rid_controller.lower() in title_lower:
                if title not in matched:
                    matched.append(title)
                break
            title_controller = _extract_controller(title)
            if title_controller and title_controller.lower() in rid_lower:
                if title not in matched:
                    matched.append(title)
                break

            # 5. Token overlap: if 3+ significant tokens match (handles
            #    "HAN University of Applied Sciences" vs "Arnhem and Nijmegen
            #    University of Applied Sciences")
            _stop = {"the", "of", "and", "in", "for", "a", "an", "by", "on",
                     "data", "protection", "authority", "dpa", "supervisory",
                     "national", "commission", "office", "commissioner",
                     "federal", "personal", "information",
                     # Countries
                     "france", "germany", "spain", "italy", "belgium",
                     "netherlands", "austria", "greece", "poland", "ireland",
                     "sweden", "denmark", "finland", "norway", "portugal",
                     "hungary", "romania", "czech", "slovakia", "slovenia",
                     "croatia", "bulgaria", "latvia", "lithuania", "estonia",
                     "malta", "cyprus", "luxembourg", "liechtenstein",
                     "french", "spanish", "italian", "dutch", "greek",
                     "german", "belgian", "swedish", "danish", "norwegian",
                     # DPA names and common words in their titles
                     "cnil", "aepd", "garante", "apd", "gba", "bfdi",
                     "datatilsynet", "unknown", "gdpr",
                     "per", "dei", "dati", "personali", "protezione",
                     "hellenic", "supervisory", "processing",
                     "freedom", "telecoms", "provider"}
            rid_tokens = {w for w in re.findall(r'\b\w{3,}\b', rid_lower) if w not in _stop}
            title_tokens = {w for w in re.findall(r'\b\w{3,}\b', title_lower) if w not in _stop}
            overlap = rid_tokens & title_tokens
            if len(overlap) >= 3:
                if title not in matched:
                    matched.append(title)
                break

    return matched


EVAL_SYSTEM_PROMPT = """\
You are a GDPR enforcement search assistant. Your ONLY job is to find
the most relevant enforcement decisions for the user's query.

Use your search tools strategically:
- search_precedents: semantic + filtered search (full RAG pipeline)
- search_by_article: SQL lookup by GDPR article number
- search_by_entity: SQL lookup by company/controller name

Strategy:
1. Identify what type of query it is (entity, article, conceptual).
2. Use the BEST tool for that type first.
3. ALWAYS use at least 2 different tools or angles before responding.
   - If query mentions a company/entity: try search_by_entity AND search_precedents.
   - If query mentions a GDPR article: try search_by_article AND search_precedents.
   - If conceptual: try search_precedents with different phrasings or angles.
4. If first search returns generic results (not the specific case asked about),
   reformulate and try a different tool. Do NOT give up after one search.
5. Once you have results that clearly match the specific case/entity, respond.

IMPORTANT: 2-3 tool calls is the sweet spot. NEVER exceed 6 tool calls total.
If after 3-4 searches you haven't found the specific case, respond with what
you have. Do NOT call simulate_fine, dpa_profile, lookup_law, or memory tools.
"""


def _create_eval_agent(conn: psycopg.Connection, recalldb_memory=None):
    """Create a slim agent with only search tools (cheaper, faster)."""
    import os
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model="moonshotai/kimi-k2",
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
            max_tokens=1024,  # short responses — we only care about retrieval
            temperature=0.0,
        )
    else:
        raise RuntimeError("OPENROUTER_API_KEY required for agent eval")

    all_tools = create_tools(conn, recalldb_memory=recalldb_memory)
    search_tools = [t for t in all_tools if t.name in (
        "search_precedents", "search_by_article", "search_by_entity",
    )]

    return create_react_agent(
        model=llm,
        tools=search_tools,
        prompt=EVAL_SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )


def _eval_single_query(
    item: dict,
    idx: int,
    total: int,
    tracker_map: dict,
    db_url: str,
    recalldb_memory=None,
) -> dict:
    """Evaluate a single query — thread-safe (own DB conn + agent)."""
    qid = item["id"]
    question = item["question"]
    relevant_raw = item["relevant_source_ids"]
    relevant = resolve_relevant_ids(relevant_raw, tracker_map)

    log.info("[%d/%d] %s — %s", idx, total, qid, question[:80])

    conn = psycopg.connect(db_url, autocommit=True)
    agent = _create_eval_agent(conn, recalldb_memory=recalldb_memory)

    t0 = time.monotonic()
    try:
        thread_id = f"eval-{qid}"
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 14}
        invoke_result = agent.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=config,
        )
        final = invoke_result["messages"][-1]
        result = {
            "content": final.content,
            "thread_id": thread_id,
            "messages_count": len(invoke_result["messages"]),
        }
        elapsed_ms = int((time.monotonic() - t0) * 1000)
    except Exception as e:
        log.error("  [%s] Agent error: %s", qid, e)
        conn.close()
        return {"id": qid, "error": str(e)}

    # Extract titles from conversation state
    all_messages = []
    try:
        state = agent.get_state({"configurable": {"thread_id": f"eval-{qid}"}})
        all_messages = state.values.get("messages", [])
    except Exception:
        all_messages = []

    conn.close()

    retrieved_titles = extract_titles_from_messages(all_messages)
    fuzzy_extra = fuzzy_match(retrieved_titles, relevant)

    # Build full trace for debugging — every LLM decision and tool I/O
    trace: list[dict] = []
    tool_calls = 0
    for msg in all_messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tool_calls += len(msg.tool_calls)
            # LLM decided to call tools — capture reasoning + tool args
            reasoning = getattr(msg, "content", "") or ""
            for tc in msg.tool_calls:
                trace.append({
                    "type": "tool_call",
                    "tool": tc.get("name", "?"),
                    "args": tc.get("args", {}),
                    "reasoning": reasoning[:500] if reasoning else "",
                })
        elif hasattr(msg, "name") and msg.name:
            # Tool response
            content = getattr(msg, "content", "") or ""
            trace.append({
                "type": "tool_result",
                "tool": msg.name,
                "output_preview": content[:1000],
            })
        elif hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            content = getattr(msg, "content", "") or ""
            msg_type = type(msg).__name__
            if msg_type == "AIMessage" and content:
                trace.append({
                    "type": "llm_response",
                    "content_preview": content[:1000],
                })

    response_text = result.get("content", "")
    source_in_response = any(
        rid.lower() in response_text.lower() for rid in relevant
    )

    hr_exact = {k: hit_rate(retrieved_titles, relevant, k) for k in K_VALUES}
    rr_exact = reciprocal_rank(retrieved_titles, relevant)

    # Fuzzy match only against retrieved_titles (NOT response text).
    # The LLM may "remember" cases from training data — that's hallucination,
    # not retrieval. Only count docs the system actually retrieved.
    if hr_exact[5] == 0 and fuzzy_extra:
        hr_final = {k: 1.0 for k in K_VALUES}
        rr_final = 0.5
    else:
        hr_final = hr_exact
        rr_final = rr_exact

    # Detect tool errors and extract RAG scores from tool outputs
    rag_scores: list[dict] = []
    tool_errors: list[dict] = []
    _ERROR_PATTERNS = (
        "No matching", "No enforcement", "not found", "Invalid",
        "too generic", "Error", "Traceback",
    )
    for t in trace:
        if t.get("type") == "tool_result":
            output = t.get("output_preview", "")
            tool_name = t.get("tool", "?")

            # Detect error/empty responses from tools
            is_error = (
                not output.strip()
                or any(p in output for p in _ERROR_PATTERNS)
                and "Found" not in output
            )
            if is_error and len(output) < 200:
                tool_errors.append({
                    "tool": tool_name,
                    "message": output.strip()[:150] or "(empty response)",
                })

            # Extract scores from search tools
            if tool_name in ("search_precedents", "search_by_entity", "search_by_article"):
                for line in output.split("\n"):
                    if line.strip().startswith("**") and "**" in line.strip()[2:]:
                        title = line.strip().split("**")[1]
                        score_match = re.search(r"Score:\s*([\d.]+)", output[output.find(title):])
                        rag_scores.append({
                            "title": title,
                            "tool": tool_name,
                            "score": float(score_match.group(1)) if score_match else None,
                        })

    entry: dict = {
        "id": qid,
        "category": item.get("category", ""),
        "question": question,
        "relevant_ids": relevant,
        "relevant_ids_raw": relevant_raw,
        "retrieved_titles": retrieved_titles[:15],
        "retrieval_ms": elapsed_ms,
        "messages_count": result.get("messages_count", 0),
        "tool_calls": tool_calls,
        "hit_rate_exact": hr_exact,
        "mrr_exact": rr_exact,
        "hit_rate": hr_final,
        "mrr": rr_final,
        "source_in_response": source_in_response,
        "fuzzy_matched": bool(fuzzy_extra) and hr_exact[5] == 0,
        "rag_scores": rag_scores[:20],
        "tool_errors": tool_errors,
        "response_preview": response_text[:2000],
        "trace": trace,
    }

    status = "HIT" if hr_final[5] > 0 else "MISS"
    if tool_errors:
        status += f" (⚠ {len(tool_errors)} tool errors)"
    log.info(
        "  [%s] HR@5=%.0f  MRR=%.3f  tools=%d  msgs=%d  %s",
        qid, hr_final[5], rr_final,
        tool_calls, result.get("messages_count", 0), status,
    )

    return entry


def evaluate_agent(
    golden_set: list[dict],
    conn: psycopg.Connection,
    limit: int | None = None,
    recalldb_memory=None,
    parallel: int = 1,
) -> list[dict]:
    """Run each golden set query through the agent and measure retrieval.

    Args:
        parallel: Number of queries to run concurrently (default 1 = serial).
    """
    if limit:
        golden_set = golden_set[:limit]

    tracker_map = load_tracker_id_map()
    db_url = os.environ.get("DATABASE_URL", "")

    eval_t0 = time.monotonic()

    if parallel <= 1:
        # Serial mode (original behavior)
        results = []
        for i, item in enumerate(golden_set, start=1):
            entry = _eval_single_query(
                item, i, len(golden_set), tracker_map, db_url, recalldb_memory,
            )
            results.append(entry)
    else:
        # Parallel mode — lazy-load models on first use (RLock protects init)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        log.info("Parallel mode: %d workers — models will lazy-load on first query", parallel)

        results = [None] * len(golden_set)
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {}
            for i, item in enumerate(golden_set):
                future = executor.submit(
                    _eval_single_query,
                    item, i + 1, len(golden_set), tracker_map, db_url,
                    recalldb_memory,
                )
                futures[future] = i

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    log.error("Worker error: %s", e)
                    results[idx] = {"id": golden_set[idx]["id"], "error": str(e)}

        # Progress summary
        done = sum(1 for r in results if r is not None)
        hits = sum(1 for r in results if r and r.get("hit_rate", {}).get(5, 0) > 0)
        log.info("Parallel eval done: %d/%d queries, %d hits", done, len(golden_set), hits)

    total_s = time.monotonic() - eval_t0

    results.append({
        "_meta": True,
        "total_eval_s": round(total_s, 1),
        "total_queries": len(golden_set),
    })

    return results


def print_report(results: list[dict]) -> None:
    """Print aggregate metrics."""
    meta = next((r for r in results if r.get("_meta")), {})
    valid = [r for r in results if "error" not in r and not r.get("_meta")]
    n = len(valid)
    if n == 0:
        print("No valid results.")
        return

    print(f"\n{'=' * 65}")
    print(f"GDPRScope Agent Evaluation — {n} queries")
    print("=" * 65)

    # Retrieval metrics
    print("\n-- Retrieval Metrics (agent multi-turn) -------------------------")
    for k in K_VALUES:
        avg_hr = sum(r["hit_rate"].get(k, r["hit_rate"].get(str(k), 0)) for r in valid) / n
        print(f"  HR@{k:<2d}  : {avg_hr:.3f}  ({avg_hr*100:.1f}%)")

    avg_mrr = sum(r["mrr"] for r in valid) / n
    print(f"  MRR   : {avg_mrr:.3f}")

    # Exact vs fuzzy breakdown
    exact_hits = sum(1 for r in valid if r.get("hit_rate_exact", {}).get(5, 0) > 0)
    fuzzy_hits = sum(1 for r in valid if r.get("fuzzy_matched", False))
    response_hits = sum(1 for r in valid if r.get("source_in_response", False) and r.get("hit_rate_exact", {}).get(5, 0) == 0)
    print(f"\n  Exact title matches : {exact_hits}/{n}")
    print(f"  Fuzzy matches       : {fuzzy_hits}")
    print(f"  In response only    : {response_hits}")

    # Agent stats
    avg_tools = sum(r.get("tool_calls", 0) for r in valid) / n
    avg_msgs = sum(r.get("messages_count", 0) for r in valid) / n
    avg_ms = sum(r.get("retrieval_ms", 0) for r in valid) / n
    avg_titles = sum(len(r.get("retrieved_titles", [])) for r in valid) / n
    print(f"\n-- Agent Stats --------------------------------------------------")
    print(f"  Avg tool calls/query : {avg_tools:.1f}")
    print(f"  Avg messages/query   : {avg_msgs:.1f}")
    print(f"  Avg latency          : {avg_ms/1000:.1f}s")
    print(f"  Avg titles retrieved : {avg_titles:.1f}")

    # RAG retrieval scores (from search_precedents output)
    all_scores = []
    hit_scores = []
    miss_scores = []
    for r in valid:
        scores = r.get("rag_scores", [])
        vals = [s["score"] for s in scores if s.get("score") is not None]
        if vals:
            all_scores.extend(vals)
            if r["hit_rate"].get(5, r["hit_rate"].get("5", 0)) > 0:
                hit_scores.extend(vals)
            else:
                miss_scores.extend(vals)
    if all_scores:
        print(f"\n-- RAG Retrieval Scores ------------------------------------------")
        print(f"  All results  : avg={sum(all_scores)/len(all_scores):.3f}  "
              f"median={sorted(all_scores)[len(all_scores)//2]:.3f}  n={len(all_scores)}")
        if hit_scores:
            print(f"  HIT queries  : avg={sum(hit_scores)/len(hit_scores):.3f}  "
                  f"median={sorted(hit_scores)[len(hit_scores)//2]:.3f}  n={len(hit_scores)}")
        if miss_scores:
            print(f"  MISS queries : avg={sum(miss_scores)/len(miss_scores):.3f}  "
                  f"median={sorted(miss_scores)[len(miss_scores)//2]:.3f}  n={len(miss_scores)}")

    # Per-category
    categories = sorted({r.get("category", "") for r in valid})
    if len(categories) > 1:
        print(f"\n-- By Category --------------------------------------------------")
        for cat in categories:
            cat_r = [r for r in valid if r.get("category") == cat]
            nc = len(cat_r)
            hr5 = sum(r["hit_rate"].get(5, r["hit_rate"].get("5", 0)) for r in cat_r) / nc
            mrr = sum(r["mrr"] for r in cat_r) / nc
            tools = sum(r.get("tool_calls", 0) for r in cat_r) / nc
            print(f"  {cat:<22} n={nc:>3}  HR@5={hr5:.2f}  MRR={mrr:.3f}  tools={tools:.1f}")

    # Tool errors summary
    queries_with_errors = [r for r in valid if r.get("tool_errors")]
    if queries_with_errors:
        from collections import Counter as _Counter
        err_tools = _Counter()
        for r in queries_with_errors:
            for e in r["tool_errors"]:
                err_tools[e["tool"]] += 1
        n_err = len(queries_with_errors)
        err_hits = sum(1 for r in queries_with_errors
                       if r["hit_rate"].get(5, r["hit_rate"].get("5", 0)) > 0)
        print(f"\n-- Tool Errors ({n_err} queries affected) ----------------------------")
        print(f"  Queries with errors: {n_err}/{n} ({n_err/n*100:.0f}%)")
        print(f"  Of those, still HIT: {err_hits}/{n_err}")
        for tool_name, count in err_tools.most_common():
            print(f"    {tool_name}: {count} errors")

    # Misses
    missed = [r for r in valid if r["hit_rate"].get(5, r["hit_rate"].get("5", 0)) == 0]
    if missed:
        print(f"\n-- Misses ({len(missed)}) -------------------------------------------------")
        for r in missed[:20]:
            errs = len(r.get("tool_errors", []))
            err_flag = f" ⚠{errs}err" if errs else ""
            print(f"  [{r['id']}] {r['question'][:60]}{err_flag}")

    # Timing
    if meta.get("total_eval_s"):
        print(f"\n-- Timing -------------------------------------------------------")
        print(f"  Total eval: {meta['total_eval_s']}s ({meta['total_eval_s']/60:.1f}min)")
        print(f"  Per query : {meta['total_eval_s']/n:.1f}s avg")

    print(f"\n{'=' * 65}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="GDPRScope Agent Evaluation")
    parser.add_argument("--golden", required=True, help="Path to golden set JSON")
    parser.add_argument("--output", default=None, help="Save results to JSON")
    parser.add_argument("--limit", type=int, default=None, help="Limit to N queries")
    parser.add_argument("--category", default=None, help="Filter by category")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Run N queries concurrently (default: 1 = serial)")
    parser.add_argument("--recalldb", action="store_true",
                        help="Enable RecallDB retrieval learning")
    args = parser.parse_args()

    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL not set")

    golden_path = Path(args.golden)
    if not golden_path.exists():
        sys.exit(f"ERROR: Golden set not found: {golden_path}")

    with open(golden_path, encoding="utf-8") as f:
        golden_set = json.load(f)

    if args.category:
        golden_set = [q for q in golden_set if q.get("category") == args.category]
        log.info("Category filter '%s': %d queries", args.category, len(golden_set))

    log.info("Golden set: %d queries", len(golden_set))

    conn = psycopg.connect(DATABASE_URL, autocommit=True)

    recalldb_mem = None
    if args.recalldb:
        try:
            from recalldb import RetrievalMemory
            from db.rag import embed_query
            recalldb_mem = RetrievalMemory(
                connection_string=DATABASE_URL,
                embedding_fn=lambda text: embed_query(None, text),
                vector_dims=1024,
                domain="gdpr",
            )
            recalldb_mem.initialize()
            log.info("RecallDB enabled — retrieval learning active")
        except ImportError:
            log.error("RecallDB not installed. Run: pip install -e ../recalldb")
            sys.exit(1)

    results = evaluate_agent(
        golden_set, conn, limit=args.limit,
        recalldb_memory=recalldb_mem, parallel=args.parallel,
    )
    conn.close()

    print_report(results)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                [r for r in results if not r.get("_meta")],
                f, indent=2, ensure_ascii=False, default=str,
            )
        log.info("Results saved to %s", out_path)


if __name__ == "__main__":
    main()
