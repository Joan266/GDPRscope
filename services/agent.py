"""
GDPRScope — LangGraph ReAct Agent

Orchestrates enforcement research using 7 tools against PostgreSQL/CockroachDB.
Wraps existing services (fine_simulator, dpa_profiles, memory, rag).

Usage:
    from services.agent import create_agent, run_agent
    agent = create_agent(conn)
    result = run_agent(agent, "fintech in Spain, data breach, 50K affected")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from services.dpa_profiles import generate_dpa_profile
from services.fine_simulator import (
    SimulationInput,
    simulate_fine as _simulate_fine,
)
from services.memory import (
    get_org_profile,
    get_research_history,
    save_org_profile,
    save_research_session,
)

log = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are GDPRScope, an enforcement intelligence agent specialized in GDPR.

Your role: help DPOs and privacy lawyers assess enforcement risk by
researching real enforcement decisions, not theoretical maximums.

## Retrieval Strategy — CRITICAL

You have multiple search tools. **ALWAYS call 2-3 tools in parallel** on your
first turn to maximize coverage and minimize latency.

### First Turn — PARALLEL CALLS (mandatory)
Analyze the query and immediately call 2-3 tools simultaneously:
- search_precedents with natural language description — ALWAYS
- search_by_entity — if ANY company, organization, DPA, government body,
  university, hospital, or institution is mentioned. Even if the name is partial
  or informal (e.g., "Vodafone", "a Spanish radio station", "a Romanian housing
  association"). When in doubt, ALWAYS call search_by_entity.
- search_by_article if a GDPR article is referenced or implied
- For scenario queries: search_precedents with the LEGAL CONCEPT extracted
  (e.g., "store asking for doctor's note" → search "health data processing
  employee medical data Art. 9 special categories")

Example: "Did Vodafone get fined in Greece for SIM swap?"
→ Call ALL AT ONCE: search_precedents("Vodafone SIM swap Greece fine"),
  search_by_entity("Vodafone", context="Did Vodafone get fined in Greece for SIM swap?", jurisdiction="Greece"),
  search_by_article("32", context="Did Vodafone get fined in Greece for SIM swap?")

Example: "Can a marketing agency be held liable for GDPR violations?"
→ Call ALL AT ONCE: search_precedents("marketing agency GDPR liability processor controller"),
  search_by_entity("marketing agency", context="Can a marketing agency be held liable for GDPR violations?"),
  search_by_article("28", context="marketing agency GDPR liability processor controller")

### Smart Filtering — Use Parameters to Narrow Results
search_by_entity and search_by_article support filters. USE THEM when the query
gives you clues:
- **Fine amount mentioned** (e.g. "€150 fine") → use min_fine/max_fine to narrow
- **Country mentioned** → use jurisdiction filter
- **Year mentioned** → use year filter
- **Small/low fines** → use sort_by="fine_asc" or max_fine
- **Recent cases** → use sort_by="date_desc"
- **Only enforcement fines** → use only_with_fine=True

Examples (ALWAYS pass context= with the user's question for better ranking):
- "Vodafone fined €150 in Greece" → search_by_entity("Vodafone", context="Vodafone fined €150 in Greece", jurisdiction="Greece", max_fine=500)
- "Latest Article 32 decisions" → search_by_article("32", context="Latest Article 32 decisions", sort_by="date_desc")
- "Article 15 court rulings" → search_by_article("15", context="Article 15 court rulings")
- "Small GDPR fines under €1000" → search_by_article("5", context="Small GDPR fines under 1000", max_fine=1000, sort_by="fine_asc", only_with_fine=True)

### Query Decomposition — For Complex/Conceptual Queries
When the query involves a LEGAL CONCEPT (not a specific entity/case), decompose it
into sub-queries BEFORE searching. This is critical for conceptual, scenario, and
article_lookup queries.

**Each search_precedents call must attack a DIFFERENT ANGLE:**
- Angle A: the specific factual situation (what happened)
- Angle B: the legal principle or article at stake
- Angle C: the type of entity or sector involved
- Angle D: the outcome or consequence

Example: "Can a controller refuse access because data is blocked?"
→ Plan:
  1. search_by_article("15", context="Can a controller refuse access because data is blocked?")
  2. search_precedents("controller refuse access request grounds") — ANGLE A: factual
  3. search_precedents("data blocking restriction processing Art. 18") — ANGLE B: legal
  4. search_precedents("right of access limitation exception exemption") — ANGLE D: outcome
Execute steps 1-3 in parallel, step 4 only if needed.

Example: "Does GDPR require companies to delete data of former customers who unsubscribed?"
→ Plan:
  1. search_by_article("17", context="GDPR delete data former customers unsubscribed")
  2. search_precedents("erasure former customer data retention unsubscribe") — ANGLE A
  3. search_precedents("right to be forgotten marketing opt-out deletion") — ANGLE B
  4. search_precedents("retention period expired customer relationship ended") — ANGLE C
Execute steps 1-3 in parallel, step 4 only if needed.

### After First Results — Score-Aware Refinement
1. **Read the scores**: Check the Score values in the results.
   - If top scores are similar (e.g., all 0.3-0.4): many equally relevant results
     → synthesize across all of them, don't fixate on finding one "perfect" case
   - If scores drop sharply (e.g., #1=0.8, #3=0.2): only top results are relevant
   - If ALL scores are low (<0.2): results are poor → MUST reformulate
2. **Check the relevance tag**: HIGH/MEDIUM/LOW tells you retrieval confidence
3. **If MEDIUM or LOW**: decompose the query differently, try synonyms, or search
   by a different GDPR article that may be implied but not stated
4. **Delta search**: Only search for what the first results DON'T cover. Don't
   repeat the same type of search — try a different angle.

### Key Rules
- **Plan first for complex queries** — think about sub-concepts before searching
- **Parallel first, serial only if needed** — don't wait for results before
  starting independent searches
- **Score-aware**: use the score distribution to decide your next action
- **Max 4 search_precedents calls per query** — each call MUST use a distinct angle:
  1. Entity/case-focused (names, organizations, specific facts)
  2. Legal concept (GDPR articles, legal principles, doctrines)
  3. Factual pattern (what happened: "employee monitoring", "data breach notification delay")
  4. Outcome/consequence (fine amount, corrective measure, type of violation)
  Do NOT rephrase the same angle — if two calls cover similar ground, you're wasting a slot.
- Try different tools on failure, not the same tool rephrased
- Ask the user when stuck after 2-3 attempts

## Research Protocol

For research queries, follow this sequence:

1. READ MEMORY — Check for existing org context from previous sessions
2. UNDERSTAND — Parse the situation: articles, jurisdiction, sector, facts
3. SEARCH — Call 2-3 tools IN PARALLEL based on query type:
   - Specific company → search_by_entity(name, context=user_question) + search_precedents
   - Specific article → search_by_article(num, context=user_question) + search_precedents
   - Scenario/situation → search_precedents with LEGAL CONCEPT (not the literal
     situation) + search_by_article with implied articles
     Example: "employer reading ex-employee emails" → search "employee email
     monitoring Art. 6" + search_by_article("6", context="employer reading ex-employee emails")
   - Multiple countries → parallel searches per country
4. EVALUATE — Are the results relevant? Enough cases? If not, retry differently.
5. ENRICH — lookup_law, analyze_factors, dpa_profile, simulate_fine as needed
6. SAVE TO MEMORY — Store org profile and key findings

## Output Format

Structure your response as an **Enforcement Research Brief**:

### Executive Summary
[1-2 sentences: estimated range, confidence, number of precedents]

### Relevant Precedents
[Top 5 cases with: Case Title, DPA, Fine, Articles, Key factors, Year]

### Art. 83(2) Factor Analysis
[Which factors are mitigating/aggravating, with real impact data]

### DPA Behavioral Profile
[Median fine, trend, cases/year for this specific DPA]

### GDPR Legal Basis
[Exact article text + relevant recitals]

### Recommendations
[Data-driven recommendations based on what worked in similar cases]

### Disclaimer
Statistical range based on real enforcement data. Not legal advice.
Consult qualified legal counsel for case-specific guidance.

## Critical Rules — Grounding

- NEVER fabricate case IDs, fine amounts, or article references
- ONLY cite cases returned by your search tools — do NOT cite cases you
  "remember" from training data. If a case was not in your tool results,
  it does not exist for this conversation.
- If no precedents found, say so clearly — do not guess or invent examples
- Always show your sources (case titles, data counts)
- Use read_memory at the START, write_memory at the END
- Respond in the same language as the user query
"""


# ── Tool factory ──────────────────────────────────────────────────────────────
# Tools need a DB connection but LangChain tools are plain functions.
# We use a factory pattern: create_tools(conn) returns bound closures.

def create_tools(conn: psycopg.Connection, recalldb_memory=None) -> list:
    """Create LangGraph tools bound to a database connection.

    Args:
        conn: Database connection.
        recalldb_memory: Optional RecallDB RetrievalMemory instance for
            retrieval learning (enrichment + chunk gates + strategy).
    """

    # Track search queries across tool calls for rewrite learning
    _search_queries_log: list[dict] = []

    @tool
    def search_precedents(
        query: str,
        jurisdiction: str | None = None,
        articles: list[str] | None = None,
        sector: str | None = None,
        limit: int = 15,
    ) -> str:
        """Search GDPR enforcement decisions using the full RAG pipeline.

        Uses intent extraction, HyDE, section-aware routing, cross-encoder
        reranking, and soft-filter arms. Much more powerful than keyword search.

        Args:
            query: Natural language description of the situation
            jurisdiction: Country name (e.g. "Spain", "France", "Germany")
            articles: GDPR article numbers (e.g. ["32", "33"])
            sector: Industry sector (e.g. "Finance", "Healthcare")
            limit: Max results to return (default 10)
        """
        import time as _time
        _t0 = _time.monotonic()

        from db.rag import (
            SECTION_ROUTING,
            K_TEXT,
            K_VECTOR,
            apply_intent_filters,
            classify_query_type,
            embed_query,
            embed_query_sparse,
            expand_query,
            extract_intent,
            fetch_parent_context,
            hyde_embed,
            hyde_headnote,
            reciprocal_rank_fusion,
            rerank_by_metadata,
            rerank_with_cross_encoder,
            search_headnote_chunks,
            search_question_chunks,
            search_text_chunks,
            search_vector_by_sections,
            search_vector_chunks,
            sparse_rerank,
            _fetch_chunks_for_case_numbers,
            _fetch_fine_sorted_chunks,
            _find_controller_docs,
            OPENROUTER_API_KEY,
            ANTHROPIC_API_KEY,
            QUERY_TYPE_CONCEPTUAL,
            QUERY_TYPE_SCENARIO,
            QUERY_TYPE_CROSS_JURIS,
            QUERY_TYPE_ARTICLE,
        )

        cur = conn.cursor()
        filters: dict[str, Any] = {}
        if jurisdiction:
            filters["jurisdiction"] = jurisdiction
        if articles and len(articles) == 1:
            filters["gdpr_article"] = articles[0]

        _has_llm = bool(OPENROUTER_API_KEY or ANTHROPIC_API_KEY)

        # Intent extraction
        intent = extract_intent(query) if _has_llm else None
        if intent:
            apply_intent_filters(intent, filters)
            if intent.controller_name:
                doc_ids = _find_controller_docs(cur, intent.controller_name)
                if doc_ids:
                    filters["doc_ids"] = doc_ids

        # Classify query type
        query_type = classify_query_type(intent, query)
        is_conceptual = query_type in (
            QUERY_TYPE_CONCEPTUAL, QUERY_TYPE_SCENARIO,
            QUERY_TYPE_CROSS_JURIS, QUERY_TYPE_ARTICLE,
        )

        # HyDE embedding
        hyde_vec = None
        if _has_llm:
            if query_type in (QUERY_TYPE_CONCEPTUAL, QUERY_TYPE_SCENARIO):
                try:
                    hyde_vec = hyde_headnote(query)
                except Exception:
                    pass
            elif query_type == QUERY_TYPE_ARTICLE and intent and intent.gdpr_articles:
                try:
                    hyde_vec = hyde_embed(query, intent)
                except Exception:
                    pass

        # RecallDB: pre-retrieval enrichment + strategy
        # IMPORTANT: expansions go to BM25 text search only, NOT to vector embedding
        # Contaminating the embedding vector with expansion terms degrades retrieval
        _enrich_result = None
        _recalldb_text_query = None
        if recalldb_memory:
            try:
                _enrich_result = recalldb_memory.enrich(
                    query, query_type=query_type,
                )
                if _enrich_result.cache_tier != "none":
                    # Top-K expansion filtering is handled inside RecallDB.enrich()
                    _recalldb_text_query = _enrich_result.expanded
                    log.info("RecallDB enrich: tier=%s, +%d expansions (text only)",
                             _enrich_result.cache_tier,
                             len(_enrich_result.enrichments_used))
                if _enrich_result.strategy:
                    log.info("RecallDB strategy: %s (avg_rel=%.2f, n=%d)",
                             _enrich_result.strategy.tool_sequence,
                             _enrich_result.strategy.avg_relevance,
                             _enrich_result.strategy.n_observations)
            except Exception as e:
                log.debug("RecallDB enrich failed: %s", e)

        query_vec = embed_query(None, query)  # always embed original query, never expanded
        search_vec = hyde_vec if hyde_vec is not None else query_vec

        # RecallDB: rewrite-based search arms
        # If RecallDB learned that a similar query succeeded with a different formulation,
        # search with that formulation too and fuse results
        rewrite_arms: list[list[str]] = []
        if _enrich_result and _enrich_result.rewrites:
            for rw in _enrich_result.rewrites[:3]:  # max 3 rewrites
                try:
                    rw_vec = embed_query(None, rw.successful_query)
                    rw_hits = search_vector_chunks(cur, rw_vec, K_VECTOR, filters)
                    if rw_hits:
                        rewrite_arms.append(rw_hits)
                        log.info("RecallDB rewrite arm: '%s' → %d hits",
                                 rw.successful_query[:60], len(rw_hits))
                except Exception as e:
                    log.debug("RecallDB rewrite search failed: %s", e)

        # Article text enrichment: if articles detected, fetch legal text
        # and append to BM25 query for better keyword matching
        article_text_boost = ""
        if intent and intent.gdpr_articles:
            try:
                art_nums = intent.gdpr_articles[:3]
                placeholders = ",".join(["%s"] * len(art_nums))
                cur.execute(
                    f"SELECT article_number, title, content FROM gdpr_law "
                    f"WHERE article_number IN ({placeholders})",
                    art_nums,
                )
                for row in cur.fetchall():
                    # Use title as keyword boost (concise, relevant terms)
                    article_text_boost += f" {row[1]}"
                if article_text_boost:
                    log.info("Article text boost: %s", article_text_boost.strip()[:100])
            except Exception as e:
                log.debug("Article text fetch failed: %s", e)

        # Section-aware vector search
        target_sections = SECTION_ROUTING.get(query_type, [("holding", 10), ("facts", 10)])
        section_results = search_vector_by_sections(cur, search_vec, target_sections, filters)

        # Unfiltered vector + BM25 text (use RecallDB expansion for text only)
        vector_hits = search_vector_chunks(cur, query_vec, K_VECTOR, filters)
        bm25_query = _recalldb_text_query if _recalldb_text_query else query
        if article_text_boost:
            bm25_query = bm25_query + article_text_boost
        text_hits = search_text_chunks(cur, bm25_query, K_TEXT, filters)

        # Fine-sort injection
        fine_hits: list[str] = []
        if intent and intent.sort_by == "fine_desc":
            fine_hits = _fetch_fine_sorted_chunks(cur, K_VECTOR, filters)

        # HyPE question chunks
        question_hits = search_question_chunks(cur, query_vec, K_VECTOR, filters)

        # Case-number direct lookup
        case_hits = _fetch_chunks_for_case_numbers(cur, query)

        # Case factors search: for conceptual/scenario queries, find docs
        # with matching GDPR articles that also have case_factors extracted
        factor_hits: list[str] = []
        if is_conceptual and intent and intent.gdpr_articles:
            try:
                art_patterns = [f"%Art%{a.split('(')[0].strip()}%" for a in intent.gdpr_articles[:3]]
                art_conditions = " OR ".join(
                    "EXISTS (SELECT 1 FROM unnest(d.gdpr_articles) AS a WHERE a ILIKE %s)"
                    for _ in art_patterns
                )
                cur.execute(
                    f"SELECT DISTINCT cf.document_id FROM case_factors cf "
                    f"JOIN documents d ON d.id = cf.document_id "
                    f"WHERE ({art_conditions}) "
                    f"LIMIT 20",
                    art_patterns,
                )
                factor_doc_ids = [str(r[0]) for r in cur.fetchall()]
                if factor_doc_ids:
                    doc_placeholders = ",".join(["%s"] * len(factor_doc_ids))
                    cur.execute(
                        f"SELECT id FROM chunks WHERE document_id IN ({doc_placeholders}) "
                        f"AND chunk_type = 'child' AND embedding_version = 'bge-m3-1024' "
                        f"LIMIT 30",
                        factor_doc_ids,
                    )
                    factor_hits = [str(r[0]) for r in cur.fetchall()]
                    if factor_hits:
                        log.info("Case factors arm: %d chunks from %d docs (arts: %s)",
                                 len(factor_hits), len(factor_doc_ids),
                                 ",".join(intent.gdpr_articles[:3]))
            except Exception as e:
                log.debug("Case factors search failed: %s", e)

        # Soft-filter arms (jurisdiction + article)
        soft_arms: list[list[str]] = []
        if intent and intent.jurisdiction:
            soft_filters = {**filters, "jurisdiction": intent.jurisdiction}
            soft_juris = search_vector_chunks(cur, search_vec, 15, soft_filters)
            if soft_juris:
                soft_arms.append(soft_juris)
        if intent and intent.gdpr_articles:
            for art in intent.gdpr_articles[:2]:
                soft_filters = {**filters, "gdpr_article": art}
                soft_art = search_vector_chunks(cur, search_vec, 15, soft_filters)
                if soft_art:
                    soft_arms.append(soft_art)

        # Sparse lexical arm — BGE-M3 sparse dot product as RRF arm
        sparse_arm: list[str] = []
        try:
            query_sparse = embed_query_sparse(query)
            if query_sparse:
                all_candidates: set[str] = set()
                for arm in section_results.values():
                    all_candidates.update(arm)
                all_candidates.update(vector_hits)
                all_candidates.update(text_hits)
                if fine_hits:
                    all_candidates.update(fine_hits)
                if question_hits:
                    all_candidates.update(question_hits)
                if factor_hits:
                    all_candidates.update(factor_hits)
                for arm in soft_arms:
                    all_candidates.update(arm)
                for arm in rewrite_arms:
                    all_candidates.update(arm)
                sparse_arm = sparse_rerank(cur, query_sparse, list(all_candidates))
                if sparse_arm:
                    log.info("Sparse arm: %d candidates reranked", len(sparse_arm))
        except Exception as exc:
            log.warning("Sparse arm skipped: %s", exc)

        # N-way RRF fusion (including RecallDB rewrite arms + sparse)
        section_arms = list(section_results.values())
        rrf_ranked = reciprocal_rank_fusion(
            *section_arms,
            vector_hits, text_hits, fine_hits or None,
            question_hits or None,
            factor_hits or None,
            *(arm for arm in soft_arms),
            *(arm for arm in rewrite_arms),
            sparse_arm or None,
        )
        rrf_scores = dict(rrf_ranked)
        if case_hits:
            for cid in case_hits:
                rrf_scores[cid] = max(rrf_scores.get(cid, 0.0), 1.0)
        top_child_ids = case_hits + [cid for cid, _ in rrf_ranked[:limit * 3]
                                     if cid not in set(case_hits)]

        # Cross-encoder reranking
        non_pinned = [cid for cid in top_child_ids if cid not in set(case_hits)]
        if len(non_pinned) > limit:
            reranked = rerank_with_cross_encoder(
                cur, query, non_pinned[:30], top_n=limit * 2,
            )
            if reranked:
                reranked_ids = [cid for cid, _ in reranked]
                top_child_ids = case_hits + reranked_ids
                ce_scores = [s for _, s in reranked]
                s_min, s_max = min(ce_scores), max(ce_scores)
                span = (s_max - s_min) or 1.0
                for cid, s in reranked:
                    rrf_scores[cid] = 0.01 + 0.98 * (s - s_min) / span

        # RecallDB: apply chunk memory gates (GAM-RAG Kalman)
        if recalldb_memory:
            try:
                gate_ids = [cid for cid in top_child_ids if cid not in set(case_hits)]
                if gate_ids:
                    gates = recalldb_memory.batch_chunk_gates(
                        gate_ids, query_vec.tolist(),
                    )
                    for cid, gate_val in gates.items():
                        if cid in rrf_scores:
                            rrf_scores[cid] *= gate_val
                    # Re-sort non-pinned by gated scores
                    gated_sorted = sorted(
                        gate_ids,
                        key=lambda c: rrf_scores.get(c, 0),
                        reverse=True,
                    )
                    top_child_ids = case_hits + gated_sorted
            except Exception as e:
                log.debug("RecallDB chunk gates failed: %s", e)

        # Document links expansion (LARAG pattern):
        # If top results have linked documents, inject their chunks as candidates
        try:
            top_doc_ids = set()
            for cid in top_child_ids[:10]:
                cur.execute("SELECT document_id FROM chunks WHERE id = %s", (cid,))
                row = cur.fetchone()
                if row:
                    top_doc_ids.add(str(row[0]))
            if top_doc_ids:
                placeholders = ",".join(["%s"] * len(top_doc_ids))
                cur.execute(
                    f"SELECT DISTINCT target_document_id FROM document_links "
                    f"WHERE source_document_id IN ({placeholders}) "
                    f"UNION "
                    f"SELECT DISTINCT source_document_id FROM document_links "
                    f"WHERE target_document_id IN ({placeholders})",
                    list(top_doc_ids) + list(top_doc_ids),
                )
                linked_doc_ids = {str(r[0]) for r in cur.fetchall()} - top_doc_ids
                if linked_doc_ids:
                    link_placeholders = ",".join(["%s"] * len(linked_doc_ids))
                    cur.execute(
                        f"SELECT id FROM chunks WHERE document_id IN ({link_placeholders}) "
                        f"AND chunk_type = 'child' AND embedding_version = 'bge-m3-1024' "
                        f"LIMIT 20",
                        list(linked_doc_ids),
                    )
                    linked_chunks = [str(r[0]) for r in cur.fetchall()]
                    for cid in linked_chunks:
                        if cid not in rrf_scores:
                            # Boost slightly below lowest RRF score
                            rrf_scores[cid] = 0.05
                            top_child_ids.append(cid)
                    if linked_chunks:
                        log.info("Link expansion: +%d chunks from %d linked docs",
                                 len(linked_chunks), len(linked_doc_ids))
        except Exception as e:
            log.debug("Document link expansion failed: %s", e)

        # Fetch parent context
        contexts = fetch_parent_context(cur, top_child_ids, rrf_scores, limit)

        # Metadata rerank
        if intent and intent.sort_by:
            contexts = rerank_by_metadata(contexts, intent)

        if not contexts:
            return "No matching precedents found for this query."

        # Compute retrieval confidence from score distribution
        all_scores_sorted = sorted(rrf_scores.values(), reverse=True)
        top_scores = all_scores_sorted[:3]
        avg_top = sum(top_scores) / len(top_scores) if top_scores else 0

        # Log for cross-query rewrite learning within the session
        _search_queries_log.append({"query": query, "avg_top": avg_top})

        if avg_top >= 0.6:
            confidence = "HIGH"
        elif avg_top >= 0.3:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Score distribution analysis for the agent
        score_analysis = ""
        if len(all_scores_sorted) >= 5:
            top1 = all_scores_sorted[0]
            top5 = all_scores_sorted[4]
            top10 = all_scores_sorted[min(9, len(all_scores_sorted) - 1)]
            spread = top1 - top5
            if spread < 0.05:
                score_analysis = (
                    f"\n\n📊 Score pattern: PLATEAU (top1={top1:.3f}, top5={top5:.3f}, "
                    f"spread={spread:.3f}). Multiple results are equally relevant — "
                    f"synthesize across all rather than seeking one perfect match."
                )
            elif spread > 0.2:
                score_analysis = (
                    f"\n\n📊 Score pattern: SHARP DROP (top1={top1:.3f}, top5={top5:.3f}, "
                    f"spread={spread:.3f}). Only the top 1-2 results are strongly relevant."
                )
            else:
                score_analysis = (
                    f"\n\n📊 Score pattern: GRADUAL (top1={top1:.3f}, top5={top5:.3f}, "
                    f"top10={top10:.3f}). Good spread of relevant results."
                )

        # Format results
        results = []
        for i, ctx in enumerate(contexts, 1):
            fine_str = f"EUR {ctx['fine_amount']:,}" if ctx.get("fine_amount") else "No fine"
            arts = ", ".join(ctx.get("gdpr_articles", [])[:5])
            score = rrf_scores.get(ctx.get("child_id", ""), 0) or 0.0
            results.append(
                f"{i}. **{ctx['title']}**\n"
                f"   DPA: {ctx.get('authority', 'N/A')} ({ctx.get('jurisdiction', 'N/A')})\n"
                f"   Fine: {fine_str} | Year: {ctx.get('decision_year', 'N/A')}\n"
                f"   Articles: {arts}\n"
                f"   Outcome: {ctx.get('outcome', 'N/A')}\n"
                f"   Score: {score:.3f}"
            )

        header = f"Found {len(contexts)} precedents (relevance: {confidence}):\n\n"
        footer = ""
        if confidence == "LOW":
            footer = (
                "\n\n⚠️ LOW relevance — results may not match the query well. "
                "Consider: (1) rephrasing with different terms, "
                "(2) using search_by_article or search_by_entity instead, "
                "(3) trying broader/narrower keywords."
            )

        # Fix 5: Strategy-guided tool hint from RecallDB
        strategy_hint = ""
        if recalldb_memory and _enrich_result and _enrich_result.strategy:
            strat = _enrich_result.strategy
            if strat.avg_relevance > 0.6 and strat.n_observations >= 3:
                suggested = [t for t in strat.tool_sequence[:2]
                             if t != "search_precedents"]
                if suggested:
                    strategy_hint = (
                        f"\n\nBased on similar past queries, "
                        f"also try: {', '.join(suggested)}"
                    )

        # RecallDB: learn from this retrieval
        if recalldb_memory:
            try:
                _latency = int((_time.monotonic() - _t0) * 1000)
                # Chunk relevance: use per-chunk RRF score, not just rank
                chunks_for_learn = [
                    {
                        "chunk_id": ctx["child_id"],
                        "relevant": rrf_scores.get(ctx["child_id"], 0) > 0.5,
                    }
                    for ctx in contexts
                ]

                # P1: Extract ATOMIC enrichments (not full query)
                atomic_enrichments: list[dict] = []
                seen_terms: set[str] = set()
                query_lower = query.lower()

                # Controller from intent
                if intent and intent.controller_name:
                    ctrl = intent.controller_name
                    if ctrl not in seen_terms and len(ctrl) > 2:
                        seen_terms.add(ctrl)
                        ctrl_exps = []
                        for ctx in contexts[:3]:
                            if ctx.get("authority") and ctx["authority"] not in ctrl_exps:
                                ctrl_exps.append(ctx["authority"])
                            if ctx.get("jurisdiction") and ctx["jurisdiction"] not in ctrl_exps:
                                ctrl_exps.append(ctx["jurisdiction"])
                        if ctrl_exps:
                            atomic_enrichments.append({
                                "term": ctrl,
                                "expansions": ctrl_exps[:5],
                                "confidence": 0.6 if avg_top > 0.5 else 0.4,
                            })

                # DPA authorities and jurisdictions from results
                for ctx in contexts[:5]:
                    auth = ctx.get("authority", "")
                    juris = ctx.get("jurisdiction", "")
                    arts = [f"Article {a}" for a in ctx.get("gdpr_articles", [])[:3]]

                    if auth and len(auth) > 3 and auth not in seen_terms:
                        seen_terms.add(auth)
                        auth_exps = []
                        if juris:
                            auth_exps.append(juris)
                        auth_exps.extend(arts)
                        if auth_exps:
                            atomic_enrichments.append({
                                "term": auth,
                                "expansions": auth_exps[:5],
                                "confidence": 0.5,
                            })

                    if juris and len(juris) > 2 and juris not in seen_terms:
                        seen_terms.add(juris)
                        juris_exps = [auth] if auth else []
                        if juris_exps:
                            atomic_enrichments.append({
                                "term": juris,
                                "expansions": juris_exps,
                                "confidence": 0.5,
                            })

                # Correctness gate on enrichments is handled inside RecallDB.learn()
                recalldb_memory.learn(
                    query=query,
                    retrieved_chunks=chunks_for_learn,
                    enrichments_discovered=atomic_enrichments or None,
                    enrichments_used=(
                        _enrich_result.enrichments_used if _enrich_result else None
                    ),
                    rewrites_used=(
                        _enrich_result.rewrites
                        if _enrich_result and _enrich_result.rewrites else None
                    ),
                    query_type=query_type,
                    tools_used=["search_precedents"],
                    relevance_score=avg_top,
                    latency_ms=_latency,
                )

                # Note: confidence feedback on enrichments_used is handled
                # internally by RecallDB's learn() step 2b (asymmetric
                # +0.05/-0.10 based on relevance_score). No external call needed.

                # Cross-query rewrite learning: if this query succeeded
                # and earlier queries in the same session failed, learn
                # this formulation as a rewrite of those failed queries
                if avg_top > 0.5 and len(_search_queries_log) > 1:
                    for prev in _search_queries_log[:-1]:
                        if prev["query"] != query and prev["avg_top"] < 0.4:
                            try:
                                recalldb_memory.learn(
                                    query=prev["query"],
                                    query_rewrites=[query],
                                    relevance_score=avg_top,
                                )
                            except Exception:
                                pass
            except Exception as e:
                log.debug("RecallDB learn failed: %s", e)

        return header + "\n\n".join(results) + score_analysis + footer + strategy_hint

    @tool
    def search_by_article(
        article_number: str,
        context: str | None = None,
        jurisdiction: str | None = None,
        sort_by: str = "date_desc",
        min_fine: int | None = None,
        max_fine: int | None = None,
        year: int | None = None,
        only_with_fine: bool = False,
        limit: int = 10,
    ) -> str:
        """Search ALL decisions (enforcement + court) citing a GDPR article via SQL.

        Returns enforcement cases, DPA decisions, AND court rulings (e.g. BGH,
        CJEU). Use this when the user asks about a specific GDPR article.
        By default includes cases without fines (court decisions, reprimands).
        Set only_with_fine=True to filter to enforcement cases with fines only.

        IMPORTANT: Always pass `context` with the user's original question so
        results are ranked by relevance instead of just date.

        Args:
            article_number: Article number with sub-article if known (e.g. "32(1)", "6(1)(f)", "15(1)(g)", "5")
            context: The user's original question (used for semantic reranking)
            jurisdiction: Optional country filter (e.g. "Spain")
            sort_by: Sort order — "date_desc" (default), "date_asc", "fine_desc", "fine_asc"
            min_fine: Minimum fine amount filter (e.g. 10000)
            max_fine: Maximum fine amount filter (e.g. 50000)
            year: Filter by decision year (e.g. 2023)
            only_with_fine: Only return cases with fines (default False)
            limit: Max results (default 10)
        """
        from db.rag import embed_query, vector_to_pg

        import re
        # Keep digits, parentheses, and letters for sub-articles like 6(1)(f)
        cleaned = re.sub(r"(?i)^art(?:icle)?\.?\s*", "", article_number.strip())
        num = re.sub(r"[^0-9a-zA-Z()]", "", cleaned)
        if not num:
            return f"Invalid article number: {article_number}"

        cur = conn.cursor()
        base_num = re.split(r"[( ]", num)[0]  # "6" from "6(1)(f)"

        # Try specific sub-article first, fall back to base if too few results
        specific_pattern = f"%Art%{num}%" if num != base_num else None
        base_pattern = f"%Art%{base_num}%"

        if specific_pattern:
            cur.execute(
                "SELECT count(*) FROM documents WHERE EXISTS "
                "(SELECT 1 FROM unnest(gdpr_articles) AS a WHERE a ILIKE %s)",
                (specific_pattern,),
            )
            specific_count = cur.fetchone()[0]
            pattern = specific_pattern if specific_count >= 5 else base_pattern
        else:
            pattern = base_pattern

        where_clauses = ["EXISTS (SELECT 1 FROM unnest(d.gdpr_articles) AS a WHERE a ILIKE %s)"]
        params: list[Any] = [pattern]

        if jurisdiction:
            where_clauses.append("d.jurisdiction = %s")
            params.append(jurisdiction)
        if only_with_fine:
            where_clauses.append("d.fine_amount IS NOT NULL AND d.fine_amount > 0")
        if min_fine is not None:
            where_clauses.append("d.fine_amount >= %s")
            params.append(min_fine)
        if max_fine is not None:
            where_clauses.append("d.fine_amount <= %s")
            params.append(max_fine)
        if year is not None:
            where_clauses.append("d.decision_year = %s")
            params.append(year)

        where_sql = " AND ".join(where_clauses)

        # When context is provided, vector-rerank ALL matching docs (no CTE LIMIT —
        # arbitrary LIMIT 200 could exclude the target doc from a 2600-doc pool)
        if context:
            query_vec = vector_to_pg(embed_query(None, context))
            cur.execute(f"""
                SELECT d.title, d.authority, d.jurisdiction,
                       d.fine_amount, d.fine_currency, d.gdpr_articles,
                       d.decision_year, d.outcome, d.case_number,
                       MIN(c.embedding <=> %s::vector(1024)) AS best_dist
                FROM documents d
                JOIN chunks c ON c.document_id = d.id
                WHERE {where_sql}
                  AND c.chunk_type = 'child'
                  AND c.embedding_version = 'bge-m3-1024'
                GROUP BY d.id, d.title, d.authority, d.jurisdiction,
                         d.fine_amount, d.fine_currency, d.gdpr_articles,
                         d.decision_year, d.outcome, d.case_number
                ORDER BY best_dist
                LIMIT %s
            """, [query_vec] + params + [limit])
        else:
            sort_map = {
                "date_desc": "d.decision_date DESC NULLS LAST",
                "date_asc": "d.decision_date ASC NULLS LAST",
                "fine_desc": "d.fine_amount DESC NULLS LAST",
                "fine_asc": "d.fine_amount ASC NULLS LAST",
            }
            order = sort_map.get(sort_by, sort_map["date_desc"])
            cur.execute(f"""
                SELECT d.title, d.authority, d.jurisdiction,
                       d.fine_amount, d.fine_currency, d.gdpr_articles,
                       d.decision_year, d.outcome, d.case_number
                FROM documents d
                WHERE {where_sql}
                ORDER BY {order}
                LIMIT %s
            """, params + [limit])

        rows = cur.fetchall()
        if not rows:
            return f"No enforcement decisions found citing Article {article_number}."

        results = []
        for i, row in enumerate(rows, 1):
            title, auth, juris, fine, currency, arts, yr, outcome, case_num = row[:9]
            arts_str = ", ".join(arts[:5]) if arts else "N/A"
            fine_str = f"{currency or 'EUR'} {fine:,}" if fine else "No fine"
            results.append(
                f"{i}. **{title}**\n"
                f"   DPA: {auth or 'N/A'} ({juris or 'N/A'})\n"
                f"   Fine: {fine_str} | Year: {yr or 'N/A'}\n"
                f"   Articles: {arts_str}\n"
                f"   Outcome: {outcome or 'N/A'}"
            )

        return (
            f"Found {len(rows)} decisions citing Article {article_number}"
            f"{f' in {jurisdiction}' if jurisdiction else ''}:\n\n"
            + "\n\n".join(results)
        )

    @tool
    def search_by_entity(
        entity_name: str,
        context: str | None = None,
        jurisdiction: str | None = None,
        sort_by: str = "date_desc",
        min_fine: int | None = None,
        max_fine: int | None = None,
        year: int | None = None,
        only_with_fine: bool = False,
        limit: int = 10,
    ) -> str:
        """Search ALL decisions (enforcement + court) against a specific company or controller.

        Direct SQL lookup on controller_name — more reliable than semantic search
        for company-specific queries. Includes court rulings without fines by default.

        IMPORTANT: Always pass `context` with the user's original question so
        results are ranked by relevance instead of just date.

        Args:
            entity_name: Company or controller name (e.g. "Google", "Vodafone", "Meta")
            context: The user's original question (used for semantic reranking)
            jurisdiction: Optional country filter (e.g. "Greece", "Spain")
            sort_by: Sort order — "date_desc" (default), "date_asc", "fine_desc", "fine_asc"
            min_fine: Minimum fine amount filter (e.g. 10000)
            max_fine: Maximum fine amount filter (e.g. 500)
            year: Filter by decision year (e.g. 2022)
            only_with_fine: Only return cases with fines (default False)
            limit: Max results (default 10)
        """
        from db.rag import resolve_entity_alias, embed_query, vector_to_pg

        patterns = resolve_entity_alias(entity_name)
        if not patterns:
            return f"'{entity_name}' is too generic. Please specify a company name."

        cur = conn.cursor()
        where_clauses = ["(" + " OR ".join("d.controller_name ILIKE %s" for _ in patterns) + ")"]
        params: list[Any] = [f"%{p}%" for p in patterns]

        if jurisdiction:
            where_clauses.append("d.jurisdiction = %s")
            params.append(jurisdiction)
        if only_with_fine:
            where_clauses.append("d.fine_amount IS NOT NULL AND d.fine_amount > 0")
        if min_fine is not None:
            where_clauses.append("d.fine_amount >= %s")
            params.append(min_fine)
        if max_fine is not None:
            where_clauses.append("d.fine_amount <= %s")
            params.append(max_fine)
        if year is not None:
            where_clauses.append("d.decision_year = %s")
            params.append(year)

        where_sql = " AND ".join(where_clauses)

        if context:
            query_vec = vector_to_pg(embed_query(None, context))
            cur.execute(f"""
                SELECT d.title, d.authority, d.jurisdiction,
                       d.fine_amount, d.fine_currency, d.gdpr_articles,
                       d.decision_year, d.outcome, d.case_number,
                       d.controller_name,
                       MIN(c.embedding <=> %s::vector(1024)) AS best_dist
                FROM documents d
                JOIN chunks c ON c.document_id = d.id
                WHERE {where_sql}
                  AND c.chunk_type = 'child'
                  AND c.embedding_version = 'bge-m3-1024'
                GROUP BY d.id, d.title, d.authority, d.jurisdiction,
                         d.fine_amount, d.fine_currency, d.gdpr_articles,
                         d.decision_year, d.outcome, d.case_number,
                         d.controller_name
                ORDER BY best_dist
                LIMIT %s
            """, [query_vec] + params + [limit])
        else:
            sort_map = {
                "date_desc": "d.decision_date DESC NULLS LAST",
                "date_asc": "d.decision_date ASC NULLS LAST",
                "fine_desc": "d.fine_amount DESC NULLS LAST",
                "fine_asc": "d.fine_amount ASC NULLS LAST",
            }
            order = sort_map.get(sort_by, sort_map["date_desc"])
            cur.execute(f"""
                SELECT d.title, d.authority, d.jurisdiction,
                       d.fine_amount, d.fine_currency, d.gdpr_articles,
                       d.decision_year, d.outcome, d.case_number,
                       d.controller_name
                FROM documents d
                WHERE {where_sql}
                ORDER BY {order}
                LIMIT %s
            """, params + [limit])

        rows = cur.fetchall()
        if not rows:
            return f"No enforcement decisions found for '{entity_name}'."

        results = []
        for i, row in enumerate(rows, 1):
            (title, auth, juris, fine, currency, arts,
             yr, outcome, case_num, controller) = row[:10]
            fine_str = f"{currency or 'EUR'} {fine:,}" if fine else "No fine"
            arts_str = ", ".join(arts[:5]) if arts else "N/A"
            results.append(
                f"{i}. **{title}**\n"
                f"   Controller: {controller or 'N/A'}\n"
                f"   DPA: {auth or 'N/A'} ({juris or 'N/A'})\n"
                f"   Fine: {fine_str} | Year: {yr or 'N/A'}\n"
                f"   Articles: {arts_str}\n"
                f"   Outcome: {outcome or 'N/A'}"
            )

        return (
            f"Found {len(rows)} decisions involving '{entity_name}':\n\n"
            + "\n\n".join(results)
        )

    @tool
    def simulate_fine_tool(
        articles_violated: list[str],
        jurisdiction: str | None = None,
        sector: str | None = None,
        turnover_eur: int | None = None,
        data_subjects_affected: int | None = None,
        cooperated: bool = True,
        notified_voluntarily: bool = False,
        corrective_measures: bool = False,
        intentional: bool = False,
        prior_violations: bool = False,
    ) -> str:
        """Run EDPB 5-step fine simulation based on real enforcement data.

        Returns estimated range (P25/median/P75), methodology breakdown,
        and confidence level based on number of precedents.

        Args:
            articles_violated: GDPR articles violated (e.g. ["32", "33"])
            jurisdiction: Country (e.g. "Spain")
            sector: Industry sector (e.g. "Finance")
            turnover_eur: Annual turnover in EUR (if known)
            data_subjects_affected: Number of affected data subjects
            cooperated: Did the org cooperate with the DPA?
            notified_voluntarily: Was the breach self-reported?
            corrective_measures: Were corrective measures taken?
            intentional: Was the violation intentional?
            prior_violations: Does the org have prior GDPR violations?
        """
        params = SimulationInput(
            articles_violated=articles_violated,
            jurisdiction=jurisdiction,
            sector=sector,
            turnover_eur=turnover_eur,
            data_subjects_affected=data_subjects_affected,
            cooperated=cooperated,
            notified_voluntarily=notified_voluntarily,
            corrective_measures=corrective_measures,
            intentional=intentional,
            prior_violations=prior_violations,
        )

        result = _simulate_fine(conn, params)

        # Format range
        r = result.estimated_range
        range_str = (
            f"P25: EUR {r.get('percentile_25', 0):,} | "
            f"Median: EUR {r.get('median', 0):,} | "
            f"P75: EUR {r.get('percentile_75', 0):,}"
        )

        # Format methodology
        m = result.methodology
        method_str = (
            f"Step 1: {m.get('step1_category', 'N/A')}\n"
            f"Step 2: Starting range {m.get('step2_starting_point', {}).get('range', 'N/A')}\n"
            f"Step 3: Aggravating: {', '.join(m.get('step3_factors', {}).get('aggravating', ['None']))}\n"
            f"        Mitigating: {', '.join(m.get('step3_factors', {}).get('mitigating', ['None']))}\n"
            f"Step 4: Legal max {m.get('step4_legal_max', 'N/A')}\n"
            f"Step 5: {m.get('step5_precedent_range', 'N/A')}"
        )

        # Format precedents
        prec_lines = []
        for p in result.precedents[:5]:
            arts = ", ".join(p.articles[:3])
            prec_lines.append(
                f"  - {p.title} ({p.jurisdiction}): "
                f"EUR {p.fine_amount:,} [{arts}] "
                f"(similarity: {p.similarity_score})"
            )
        prec_str = "\n".join(prec_lines) if prec_lines else "  No matching precedents"

        # Confidence
        c = result.confidence
        conf_str = (
            f"Level: {c.get('level', 'N/A')} ({c.get('score', 0)}%) — "
            f"{c.get('explanation', '')}"
        )

        return (
            f"## Fine Simulation Result\n\n"
            f"**Estimated Range:** {range_str}\n\n"
            f"**Methodology:**\n{method_str}\n\n"
            f"**Top Precedents:**\n{prec_str}\n\n"
            f"**Confidence:** {conf_str}\n\n"
            f"**Disclaimer:** {result.disclaimer}"
        )

    @tool
    def read_memory(user_id: str) -> str:
        """Read persistent org context and previous research findings.

        Returns stored organization profile, past queries, and key findings
        from previous sessions. Use at the START of every research task.

        Args:
            user_id: User identifier
        """
        profile = get_org_profile(conn, user_id)
        history = get_research_history(conn, user_id, limit=5)

        parts = []
        if profile:
            parts.append(f"**Organization Profile:**\n{json.dumps(profile, indent=2)}")
        else:
            parts.append("No organization profile stored yet.")

        if history:
            parts.append("**Recent Research Sessions:**")
            for h in history:
                parts.append(f"  - [{h['timestamp']}] {h['query']}")
        else:
            parts.append("No previous research sessions.")

        return "\n\n".join(parts)

    @tool
    def write_memory(user_id: str, key: str, value: str) -> str:
        """Store organization context or research findings for future sessions.

        Use after completing research to save: org profile, key findings,
        risk assessments, or recommendations for continuity.

        Args:
            user_id: User identifier
            key: What to store (e.g. "org_profile", "risk_assessment")
            value: Content to store (JSON string or text)
        """
        if key == "org_profile":
            try:
                profile_data = json.loads(value)
            except json.JSONDecodeError:
                profile_data = {"raw_context": value}
            mem_id = save_org_profile(conn, user_id, profile_data)
            return f"Organization profile saved (id: {mem_id})"

        # For other keys, save as research session
        save_research_session(
            conn, user_id,
            query=f"[{key}] {value[:200]}",
            intent="agent_memory",
            filters={},
            results_summary={"key": key, "value": value[:500]},
        )
        return f"Memory saved: {key}"

    @tool
    def dpa_profile(dpa_country: str) -> str:
        """Get behavioral profile of a specific Data Protection Authority.

        Returns: median fine, fine range, total cases, cases per year,
        most sanctioned articles, year-over-year trend, and notable cases.

        Args:
            dpa_country: Country name (e.g. "Spain", "France", "Italy")
        """
        profile = generate_dpa_profile(conn, dpa_country)
        if not profile:
            return f"No enforcement data found for DPA in {dpa_country}."

        # Format profile
        arts = ", ".join(f"{a['article']} ({a['pct']}%)" for a in profile.top_articles[:5])
        sectors = ", ".join(f"{s['sector']} ({s['pct']}%)" for s in profile.top_sectors[:3])

        trend_lines = []
        for y in profile.yearly_trend:
            trend_lines.append(f"  {y['year']}: {y['count']} cases, median EUR {y['median']:,.0f}")
        trend_str = "\n".join(trend_lines) if trend_lines else "  No trend data"

        coop_str = "No cooperation data"
        if profile.cooperation_credit:
            cc = profile.cooperation_credit
            coop_str = (
                f"Cooperated: median EUR {cc['median_cooperated']:,.0f} (n={cc['n_cooperated']}), "
                f"Not cooperated: median EUR {cc['median_not_cooperated']:,.0f} (n={cc['n_not_cooperated']}), "
                f"Reduction: {cc['reduction_pct']}%"
            )

        recent = []
        for c in profile.recent_cases[:3]:
            recent.append(f"  - {c['title'][:70]}: EUR {c['fine']:,} ({c['date']})")
        recent_str = "\n".join(recent) if recent else "  No recent cases"

        return (
            f"## DPA Profile: {profile.jurisdiction}\n\n"
            f"**Cases:** {profile.total_cases} total, {profile.cases_with_fine} with fine\n"
            f"**Total fines:** EUR {profile.total_fines_eur:,}\n"
            f"**Median fine:** EUR {profile.median_fine:,.0f}\n"
            f"**Mean fine:** EUR {profile.mean_fine:,.0f}\n"
            f"**Max fine:** EUR {profile.max_fine:,} ({profile.max_fine_case[:60]})\n"
            f"**Trend:** {profile.trend_direction}\n\n"
            f"**Top Articles:** {arts}\n"
            f"**Top Sectors:** {sectors}\n\n"
            f"**Yearly Trend:**\n{trend_str}\n\n"
            f"**Cooperation Credit:** {coop_str}\n\n"
            f"**Recent Cases:**\n{recent_str}"
        )

    @tool
    def lookup_law(article_number: str) -> str:
        """Look up exact GDPR article text and related recitals.

        Returns the full legal text of the article and any recitals
        that provide interpretive context. Use to cite specific provisions.

        Args:
            article_number: Article number (e.g. "32", "5", "6")
        """
        import re
        num = re.sub(r"[^0-9]", "", article_number.split("(")[0])
        if not num:
            return f"Invalid article number: {article_number}"

        cur = conn.cursor()

        # Look up article
        cur.execute("""
            SELECT article_number, article_title, chapter, full_text
            FROM gdpr_law
            WHERE article_number LIKE %s
            ORDER BY article_number
            LIMIT 3
        """, (f"%{num}%",))
        articles = cur.fetchall()

        if not articles:
            return f"Article {article_number} not found in GDPR law database."

        parts = []
        for art in articles:
            parts.append(
                f"**{art[0]}: {art[1]}**\n"
                f"Chapter: {art[2]}\n\n"
                f"{art[3]}"
            )

        # Look up related recitals (by text search)
        cur.execute("""
            SELECT recital_number, full_text
            FROM gdpr_recitals
            WHERE full_text ILIKE %s
            ORDER BY recital_number
            LIMIT 5
        """, (f"%article {num}%",))
        recitals = cur.fetchall()

        if recitals:
            parts.append("\n**Related Recitals:**")
            for rec in recitals:
                # Truncate long recitals
                text = rec[1][:500] + "..." if len(rec[1]) > 500 else rec[1]
                parts.append(f"\nRecital ({rec[0]}): {text}")

        return "\n\n".join(parts)

    @tool
    def analyze_factors(
        articles: list[str],
        jurisdiction: str | None = None,
    ) -> str:
        """Analyze Art. 83(2) aggravating/mitigating factors from similar cases.

        Returns which factors appeared, their direction (aggravating/mitigating),
        average impact on fine amount, and frequency across cases.

        Args:
            articles: GDPR articles to analyze (e.g. ["32", "33"])
            jurisdiction: Optionally scope to a specific country
        """
        cur = conn.cursor()

        # Get factor distribution for matching cases
        art_conditions = []
        art_params = []
        for a in articles:
            num = a.replace("Art. ", "").replace("Article ", "").split("(")[0].strip()
            art_conditions.append("array_to_string(d.gdpr_articles, '||') LIKE %s")
            art_params.append(f"%Art%{num}%")

        art_clause = f"({' OR '.join(art_conditions)})" if art_conditions else "TRUE"
        extra_clause = ""
        extra_params: list = []
        if jurisdiction:
            extra_clause = "AND d.jurisdiction = %s"
            extra_params = [jurisdiction]

        # Overall assessment distribution
        cur.execute(f"""
            SELECT cf.overall_assessment, count(*) as n
            FROM case_factors cf
            JOIN documents d ON d.id = cf.document_id
            WHERE d.fine_amount > 0
              AND {art_clause}
              {extra_clause}
            GROUP BY cf.overall_assessment
            ORDER BY n DESC
        """, art_params + extra_params)
        assessments = cur.fetchall()

        # Cooperation stats
        cur.execute(f"""
            SELECT
                (cf.factor_f_cooperation->>'cooperated')::boolean as coop,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median,
                count(*)
            FROM case_factors cf
            JOIN documents d ON d.id = cf.document_id
            WHERE d.fine_amount > 0
              AND cf.factor_f_cooperation->>'cooperated' IS NOT NULL
              AND {art_clause}
              {extra_clause}
            GROUP BY 1
        """, art_params + extra_params)
        coop_rows = cur.fetchall()

        # Intent stats
        cur.execute(f"""
            SELECT
                cf.factor_b_intent->>'type' as intent_type,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY d.fine_amount) as median,
                count(*)
            FROM case_factors cf
            JOIN documents d ON d.id = cf.document_id
            WHERE d.fine_amount > 0
              AND cf.factor_b_intent->>'type' IN ('intentional', 'negligent')
              AND {art_clause}
              {extra_clause}
            GROUP BY 1
        """, art_params + extra_params)
        intent_rows = cur.fetchall()

        # Format results
        parts = [f"## Art. 83(2) Factor Analysis for Articles {', '.join(articles)}"]
        if jurisdiction:
            parts[0] += f" in {jurisdiction}"

        if assessments:
            parts.append("\n**Overall Assessment Distribution:**")
            for row in assessments:
                parts.append(f"  - {row[0] or 'Unknown'}: {row[1]} cases")

        if coop_rows:
            parts.append("\n**Cooperation Impact:**")
            for row in coop_rows:
                label = "Cooperated" if row[0] else "Not cooperated"
                parts.append(f"  - {label}: median EUR {row[1]:,.0f} ({row[2]} cases)")

        if intent_rows:
            parts.append("\n**Intent Analysis:**")
            for row in intent_rows:
                parts.append(f"  - {row[0].title()}: median EUR {row[1]:,.0f} ({row[2]} cases)")

        if not assessments and not coop_rows and not intent_rows:
            parts.append("\nNo Art. 83(2) factor data found for these articles.")

        return "\n".join(parts)

    return [
        search_precedents,
        search_by_article,
        search_by_entity,
        simulate_fine_tool,
        read_memory,
        write_memory,
        dpa_profile,
        lookup_law,
        analyze_factors,
    ]


# ── Agent creation ────────────────────────────────────────────────────────────

def create_agent(
    conn: psycopg.Connection,
    model_name: str | None = None,
    recalldb_memory=None,
) -> Any:
    """Create a LangGraph ReAct agent with all tools bound to the DB connection.

    Uses in-memory checkpointer for dev. Swap to CockroachDBSaver for prod.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    if openrouter_key:
        from langchain_openai import ChatOpenAI
        model = model_name or "moonshotai/kimi-k2"
        llm = ChatOpenAI(
            model=model,
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
            max_tokens=4096,
            temperature=0.0,
        )
    elif anthropic_key:
        from langchain_anthropic import ChatAnthropic
        model = model_name or "claude-haiku-4-5-20251001"
        llm = ChatAnthropic(
            model=model,
            api_key=anthropic_key,
            max_tokens=4096,
            temperature=0.0,
        )
    else:
        raise RuntimeError("No LLM API key available (OPENROUTER_API_KEY or ANTHROPIC_API_KEY)")

    tools = create_tools(conn, recalldb_memory=recalldb_memory)

    # In-memory checkpointer for dev — swap to CockroachDBSaver for prod
    checkpointer = MemorySaver()

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    log.info("Agent created with model=%s, %d tools", model, len(tools))
    return agent


def run_agent(
    agent: Any,
    query: str,
    user_id: str = "default",
    thread_id: str | None = None,
) -> dict:
    """Run a single query through the agent. Returns the final message content."""
    import uuid
    tid = thread_id or f"session-{user_id}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": tid}}

    result = agent.invoke(
        {"messages": [HumanMessage(content=query)]},
        config=config,
    )

    final = result["messages"][-1]
    return {
        "content": final.content,
        "thread_id": tid,
        "messages_count": len(result["messages"]),
    }


def stream_agent(
    agent: Any,
    query: str,
    user_id: str = "default",
    thread_id: str | None = None,
):
    """Stream agent execution, yielding tool calls and final response.

    Yields dicts with:
      {"type": "tool_call", "name": "...", "args": {...}}
      {"type": "tool_result", "name": "...", "content": "..."}
      {"type": "response", "content": "..."}
    """
    import uuid
    tid = thread_id or f"session-{user_id}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": tid}}

    for chunk in agent.stream(
        {"messages": [HumanMessage(content=query)]},
        config=config,
        stream_mode="updates",
    ):
        if "agent" in chunk:
            for msg in chunk["agent"].get("messages", []):
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield {
                            "type": "tool_call",
                            "name": tc["name"],
                            "args": tc["args"],
                        }
                elif hasattr(msg, "content") and msg.content:
                    yield {
                        "type": "response",
                        "content": msg.content,
                        "thread_id": tid,
                    }

        if "tools" in chunk:
            for msg in chunk["tools"].get("messages", []):
                yield {
                    "type": "tool_result",
                    "name": msg.name if hasattr(msg, "name") else "unknown",
                    "content": msg.content[:200] if hasattr(msg, "content") else "",
                }


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")

    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

    query = " ".join(sys.argv[1:]) or "Fintech in Spain, data breach, 50K users affected, we cooperated"
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"{'='*60}\n")

    agent = create_agent(conn)

    print("Streaming agent response...\n")
    for event in stream_agent(agent, query):
        if event["type"] == "tool_call":
            print(f"  [TOOL] {event['name']}({json.dumps(event['args'], indent=2)[:200]})")
        elif event["type"] == "tool_result":
            print(f"  [RESULT] {event['name']}: {event['content'][:100]}...")
        elif event["type"] == "response":
            print(f"\n{event['content']}")

    conn.close()
