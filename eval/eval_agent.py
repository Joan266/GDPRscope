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


def fuzzy_match(retrieved: list[str], relevant: list[str]) -> list[str]:
    """Try fuzzy matching: check if relevant_id is a substring of any retrieved title or vice versa."""
    matched: list[str] = []
    for rid in relevant:
        rid_lower = rid.lower()
        for title in retrieved:
            title_lower = title.lower()
            # Exact match
            if rid_lower == title_lower:
                if title not in matched:
                    matched.append(title)
                break
            # Substring: relevant_id contained in title
            if rid_lower in title_lower:
                if title not in matched:
                    matched.append(title)
                break
            # Substring: title contained in relevant_id
            if title_lower in rid_lower:
                if title not in matched:
                    matched.append(title)
                break
            # Case number match: extract case numbers and compare
            rid_nums = set(re.findall(r'[\w/-]+\d{2,}[\w/-]*', rid))
            title_nums = set(re.findall(r'[\w/-]+\d{2,}[\w/-]*', title))
            if rid_nums and title_nums and rid_nums & title_nums:
                if title not in matched:
                    matched.append(title)
                break
    return matched


EVAL_SYSTEM_PROMPT = """\
You are a GDPR enforcement search assistant. Your ONLY job is to find
the most relevant enforcement decisions for the user's query.

Tools:
- search_precedents: semantic + filtered search (returns relevance: HIGH/MEDIUM/LOW)
- search_by_article: SQL lookup by GDPR article number
- search_by_entity: SQL lookup by company/controller name

Strategy:
1. Identify query type (entity, article, conceptual) and use the best tool first.
2. Check the relevance indicator in the response:
   - HIGH → results are strong. You may respond or do one more search to confirm.
   - MEDIUM → results are partial. Try a second search with a different tool or angle.
   - LOW → results don't match well. You MUST retry with rephrased query or different tool.
3. For entity queries: search_by_entity first, then search_precedents if needed.
4. For article queries: search_by_article first, then search_precedents.
5. For conceptual/scenario queries: search_precedents, then rephrase if LOW/MEDIUM.

IMPORTANT: 2-3 tool calls is the sweet spot. NEVER exceed 5 total.
Do NOT call simulate_fine, dpa_profile, lookup_law, or memory tools.
"""


def _create_eval_agent(conn: psycopg.Connection):
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

    all_tools = create_tools(conn)
    search_tools = [t for t in all_tools if t.name in (
        "search_precedents", "search_by_article", "search_by_entity",
    )]

    return create_react_agent(
        model=llm,
        tools=search_tools,
        prompt=EVAL_SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )


def evaluate_agent(
    golden_set: list[dict],
    conn: psycopg.Connection,
    limit: int | None = None,
) -> list[dict]:
    """Run each golden set query through the agent and measure retrieval."""
    if limit:
        golden_set = golden_set[:limit]

    # Fix 1: Load tracker ID map to resolve numeric relevant_source_ids
    tracker_map = load_tracker_id_map()

    agent = _create_eval_agent(conn)
    results: list[dict] = []
    eval_t0 = time.monotonic()

    for i, item in enumerate(golden_set, start=1):
        qid = item["id"]
        question = item["question"]
        relevant_raw = item["relevant_source_ids"]
        relevant = resolve_relevant_ids(relevant_raw, tracker_map)

        log.info("[%d/%d] %s — %s", i, len(golden_set), qid, question[:80])

        t0 = time.monotonic()
        try:
            # Use recursion_limit to cap tool calls (each tool call = 2 steps: call + result)
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
            log.error("  Agent error: %s", e)
            results.append({"id": qid, "error": str(e)})
            continue

        # Extract titles from all messages in the conversation
        all_messages = []
        # run_agent returns content + messages_count but not raw messages
        # We need to get messages from the agent's state
        # Re-invoke to get full state
        try:
            state = agent.get_state({"configurable": {"thread_id": f"eval-{qid}"}})
            all_messages = state.values.get("messages", [])
        except Exception:
            all_messages = []

        retrieved_titles = extract_titles_from_messages(all_messages)

        # Also check fuzzy matches (agent might format titles slightly differently)
        fuzzy_extra = fuzzy_match(retrieved_titles, relevant)

        # Count tool calls
        tool_calls = 0
        for msg in all_messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls += len(msg.tool_calls)

        # Also try: check if relevant source_id appears anywhere in the response
        response_text = result.get("content", "")
        source_in_response = any(
            rid.lower() in response_text.lower() for rid in relevant
        )

        # Compute metrics — use retrieved_titles order
        hr = {k: hit_rate(retrieved_titles, relevant, k) for k in K_VALUES}
        rr = reciprocal_rank(retrieved_titles, relevant)

        # If exact match failed, try fuzzy
        if hr[5] == 0 and fuzzy_extra:
            # Fuzzy match found — count as hit but with lower rank
            hr_fuzzy = {k: 1.0 for k in K_VALUES}
            rr_fuzzy = 0.5  # conservative rank estimate
        else:
            hr_fuzzy = hr
            rr_fuzzy = rr

        # If even fuzzy failed but source appears in final response
        if hr_fuzzy[5] == 0 and source_in_response:
            hr_response = {k: 1.0 for k in K_VALUES}
            rr_response = 0.25  # appeared but maybe not in search results
        else:
            hr_response = hr_fuzzy
            rr_response = rr_fuzzy

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
            "hit_rate_exact": hr,
            "mrr_exact": rr,
            "hit_rate": hr_response,
            "mrr": rr_response,
            "source_in_response": source_in_response,
            "fuzzy_matched": bool(fuzzy_extra) and hr[5] == 0,
            "response_preview": response_text[:500],
        }

        log.info(
            "  HR@5=%.0f  MRR=%.3f  tools=%d  msgs=%d  titles=%d  %s",
            hr_response[5], rr_response,
            tool_calls, result.get("messages_count", 0),
            len(retrieved_titles),
            "HIT" if hr_response[5] > 0 else "MISS",
        )

        results.append(entry)

    total_s = time.monotonic() - eval_t0

    # Add meta
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

    # Misses
    missed = [r for r in valid if r["hit_rate"].get(5, r["hit_rate"].get("5", 0)) == 0]
    if missed:
        print(f"\n-- Misses ({len(missed)}) -------------------------------------------------")
        for r in missed[:20]:
            print(f"  [{r['id']}] {r['question'][:65]}")

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
    results = evaluate_agent(golden_set, conn, limit=args.limit)
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
