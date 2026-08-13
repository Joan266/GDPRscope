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

You have multiple search tools. Use them strategically:

1. **Start broad, then narrow**: First search_precedents with natural language.
   If results are poor or too few, retry with different keywords or filters.

2. **Decompose complex queries**: If the user asks about multiple countries,
   articles, or topics, break into separate searches and combine results.
   Example: "Italy vs Spain telecom fines" → search Italy telecom, then Spain telecom.

3. **Try different angles on failure**: If semantic search returns irrelevant results:
   - Use search_by_article for article-specific queries (SQL, not embeddings)
   - Use search_by_entity for company-specific queries
   - Rephrase and search again with different terms
   - Remove filters that may be too restrictive

4. **Self-reflect after each search**: Check if the results actually answer
   the user's question. If not, explain what you found and what's missing,
   then try a different approach or ask the user for clarification.

5. **Ask the user when stuck**: If after 2-3 search attempts you still lack
   good results, ask the user for more details (country? company? article?
   time period?) rather than guessing.

## Research Protocol

For research queries, follow this sequence:

1. READ MEMORY — Check for existing org context from previous sessions
2. UNDERSTAND — Parse the situation: articles, jurisdiction, sector, facts
3. SEARCH — Use the best tool(s) for the query type:
   - Specific company → search_by_entity
   - Specific article → search_by_article
   - General/conceptual → search_precedents
   - Multiple countries → decompose into separate searches
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

## Critical Rules

- NEVER fabricate case IDs, fine amounts, or article references
- ONLY cite cases returned by your search tools
- If no precedents found, say so clearly — do not guess
- Always show your sources (case titles, data counts)
- Use read_memory at the START, write_memory at the END
- Respond in the same language as the user query
"""


# ── Tool factory ──────────────────────────────────────────────────────────────
# Tools need a DB connection but LangChain tools are plain functions.
# We use a factory pattern: create_tools(conn) returns bound closures.

def create_tools(conn: psycopg.Connection) -> list:
    """Create LangGraph tools bound to a database connection."""

    @tool
    def search_precedents(
        query: str,
        jurisdiction: str | None = None,
        articles: list[str] | None = None,
        sector: str | None = None,
        limit: int = 10,
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
        from db.rag import (
            SECTION_ROUTING,
            K_TEXT,
            K_VECTOR,
            apply_intent_filters,
            classify_query_type,
            embed_query,
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

        query_vec = embed_query(None, query)
        search_vec = hyde_vec if hyde_vec is not None else query_vec

        # Section-aware vector search
        target_sections = SECTION_ROUTING.get(query_type, [("holding", 10), ("headnote", 10)])
        section_results = search_vector_by_sections(cur, search_vec, target_sections, filters)

        # Unfiltered vector + BM25 text
        vector_hits = search_vector_chunks(cur, query_vec, K_VECTOR, filters)
        text_hits = search_text_chunks(cur, query, K_TEXT, filters)

        # Fine-sort injection
        fine_hits: list[str] = []
        if intent and intent.sort_by == "fine_desc":
            fine_hits = _fetch_fine_sorted_chunks(cur, K_VECTOR, filters)

        # HyPE question chunks
        question_hits = search_question_chunks(cur, query_vec, K_VECTOR, filters)

        # Case-number direct lookup
        case_hits = _fetch_chunks_for_case_numbers(cur, query)

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

        # N-way RRF fusion
        section_arms = list(section_results.values())
        rrf_ranked = reciprocal_rank_fusion(
            *section_arms,
            vector_hits, text_hits, fine_hits or None,
            question_hits or None,
            *(arm for arm in soft_arms),
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

        # Fetch parent context
        contexts = fetch_parent_context(cur, top_child_ids, rrf_scores, limit)

        # Metadata rerank
        if intent and intent.sort_by:
            contexts = rerank_by_metadata(contexts, intent)

        if not contexts:
            return "No matching precedents found for this query."

        # Compute retrieval confidence from top scores
        top_scores = sorted(rrf_scores.values(), reverse=True)[:3]
        avg_top = sum(top_scores) / len(top_scores) if top_scores else 0
        if avg_top >= 0.6:
            confidence = "HIGH"
        elif avg_top >= 0.3:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Format results
        results = []
        for i, ctx in enumerate(contexts, 1):
            fine_str = f"EUR {ctx['fine_amount']:,}" if ctx.get("fine_amount") else "No fine"
            arts = ", ".join(ctx.get("gdpr_articles", [])[:5])
            results.append(
                f"{i}. **{ctx['title']}**\n"
                f"   DPA: {ctx.get('authority', 'N/A')} ({ctx.get('jurisdiction', 'N/A')})\n"
                f"   Fine: {fine_str} | Year: {ctx.get('decision_year', 'N/A')}\n"
                f"   Articles: {arts}\n"
                f"   Outcome: {ctx.get('outcome', 'N/A')}"
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
        return header + "\n\n".join(results) + footer

    @tool
    def search_by_article(
        article_number: str,
        jurisdiction: str | None = None,
        limit: int = 10,
    ) -> str:
        """Search enforcement decisions that cite a specific GDPR article via SQL.

        This is a DIRECT database lookup — much more reliable than semantic search
        for article-specific queries. Use this when the user asks about a specific
        GDPR article (e.g. "Article 32 fines", "Art. 17 enforcement").

        Args:
            article_number: Article number (e.g. "32", "5", "6(1)")
            jurisdiction: Optional country filter (e.g. "Spain")
            limit: Max results (default 10)
        """
        import re
        num = re.sub(r"[^0-9()]", "", article_number)
        if not num:
            return f"Invalid article number: {article_number}"

        cur = conn.cursor()
        pattern = f"%Art%{num.split('(')[0]}%"

        juris_clause = ""
        params: list[Any] = [pattern]
        if jurisdiction:
            juris_clause = "AND d.jurisdiction = %s"
            params.append(jurisdiction)

        cur.execute(f"""
            SELECT d.title, d.authority, d.jurisdiction,
                   d.fine_amount, d.fine_currency, d.gdpr_articles,
                   d.decision_year, d.outcome, d.case_number
            FROM documents d
            WHERE EXISTS (
                SELECT 1 FROM unnest(d.gdpr_articles) AS a WHERE a ILIKE %s
            )
            {juris_clause}
            AND d.fine_amount IS NOT NULL AND d.fine_amount > 0
            ORDER BY d.fine_amount DESC
            LIMIT %s
        """, params + [limit])

        rows = cur.fetchall()
        if not rows:
            return f"No enforcement decisions found citing Article {article_number}."

        results = []
        for i, row in enumerate(rows, 1):
            title, auth, juris, fine, currency, arts, year, outcome, case_num = row
            arts_str = ", ".join(arts[:5]) if arts else "N/A"
            results.append(
                f"{i}. **{title}**\n"
                f"   DPA: {auth or 'N/A'} ({juris or 'N/A'})\n"
                f"   Fine: {currency or 'EUR'} {fine:,} | Year: {year or 'N/A'}\n"
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
        limit: int = 10,
    ) -> str:
        """Search enforcement decisions against a specific company or controller.

        Direct SQL lookup on controller_name — more reliable than semantic search
        for company-specific queries. Use when the user asks about a specific
        organization (e.g. "Google fines", "what happened to Vodafone").

        Args:
            entity_name: Company or controller name (e.g. "Google", "Vodafone", "Meta")
            limit: Max results (default 10)
        """
        from db.rag import resolve_entity_alias

        patterns = resolve_entity_alias(entity_name)
        if not patterns:
            return f"'{entity_name}' is too generic. Please specify a company name."

        cur = conn.cursor()
        placeholders = " OR ".join("d.controller_name ILIKE %s" for _ in patterns)
        params = [f"%{p}%" for p in patterns]

        cur.execute(f"""
            SELECT d.title, d.authority, d.jurisdiction,
                   d.fine_amount, d.fine_currency, d.gdpr_articles,
                   d.decision_year, d.outcome, d.case_number,
                   d.controller_name
            FROM documents d
            WHERE ({placeholders})
            ORDER BY d.fine_amount DESC NULLS LAST
            LIMIT %s
        """, params + [limit])

        rows = cur.fetchall()
        if not rows:
            return f"No enforcement decisions found for '{entity_name}'."

        results = []
        for i, row in enumerate(rows, 1):
            (title, auth, juris, fine, currency, arts,
             year, outcome, case_num, controller) = row
            fine_str = f"{currency or 'EUR'} {fine:,}" if fine else "No fine"
            arts_str = ", ".join(arts[:5]) if arts else "N/A"
            results.append(
                f"{i}. **{title}**\n"
                f"   Controller: {controller or 'N/A'}\n"
                f"   DPA: {auth or 'N/A'} ({juris or 'N/A'})\n"
                f"   Fine: {fine_str} | Year: {year or 'N/A'}\n"
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

    tools = create_tools(conn)

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
