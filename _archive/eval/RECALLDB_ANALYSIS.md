# RecallDB A/B Test Analysis — Deep Dive

## 1. Test Setup

```
Baseline:  Agent (no RecallDB)  → 20 queries × golden_set_v4_mini
Cold:      Agent + RecallDB     → 20 queries, empty memory
Warm:      Agent + RecallDB     → 20 queries, memory from Cold run

All runs: same queries, same order, same LLM (Kimi K2 via OpenRouter, temp=0.0)
```

## 2. Aggregate Results

```
                          Baseline       Cold       Warm
HR@5.....................    75.0%      70.0%      75.0%
MRR......................    0.467      0.463      0.562  (+20.3%)
Tools/query..............      3.8        3.9        3.5  (-7.9%)
Latency (s)..............    113.7      108.8      104.6  (-8.0%)
```

## 3. Per-Query Breakdown

### IMPROVED (Warm vs Baseline): 3 new HITs + 3 MRR gains

| Query | Cat | Change | Detail |
|-------|-----|--------|--------|
| gs4-038 | conceptual | MISS→HIT | "condominium display court judgments" — enrichments added AEPD, Article 6 |
| gs4-040 | conceptual | MISS→HIT | "telecom refuse SAR" — enrichments added Article 12, Article 15 |
| gs4-066 | scenario | MISS→HIT | "video of minor online" — enrichments added Garante, Article 6(1)(f) |
| gs4-037 | conceptual | MRR +0.5 | Found doc at rank 1 instead of rank 2. Tools 6→5 |
| gs4-093 | fine_lookup | MRR +0.17 | "Dedalus Biologie" — Article 28 enrichment helped |
| gs4-096 | fine_lookup | MRR +0.5 | "orthodontic practice" — Dutch DPA enrichment helped |

### REGRESSED (Warm vs Baseline): 3 new MISSes

| Query | Cat | Change | Detail |
|-------|-----|--------|--------|
| gs4-029 | article_lookup | HIT→MISS | "Article 14 GDPR" — LLM variance (tools 2→3, different search terms) |
| gs4-031 | article_lookup | HIT→MISS | "landlord third parties" — LLM variance (tools 5→2, under-searched) |
| gs4-065 | scenario | HIT→MISS | "employer delete data" — LLM variance (tools 4→3) |

### PERSISTENT MISSES (all 3 runs)

| Query | Cat | Root Cause |
|-------|-----|-----------|
| gs4-001 | named_entity | Semantic gap: "payment company" ≠ "SAN-2021-020". CNIL has 50+ decisions, agent finds wrong one |
| gs4-067 | scenario | Jurisdiction mismatch: expects AEPD Spain, agent finds Italy/Denmark/Belgium decisions |

## 4. RecallDB Internal State After 2 Runs

```
Enrichments:  163 total, only 9 ever used (5.5% hit rate)
Strategies:   3 types (article, entity, conceptual), all → ["search_precedents"]
Chunk memory: 940 chunks, avg perplexity=0.769
  - 201 high-confidence (perp<0.5)
  - 466 low-confidence (perp>0.9)
Query log:    174 entries
```

## 5. DIAGNOSED PROBLEMS

### P1: Enrichment terms are entire queries, not atomic terms
```
Current:  term = "CNIL payment company fined customer data testing unsecured server"
          expansions = ["Article 5(1)(c)", "Article 32", "CNIL (France)", "France"]

Should be: term = "CNIL"
           expansions = ["Commission Nationale de l'Informatique et des Libertes", "France"]

           term = "SIM swap"
           expansions = ["SIM card replacement", "fraud", "identity theft"]
```
Problem: Full query as term means SIM_PARTIAL threshold (0.60 cosine) rarely matches
a *different* query. Only near-identical queries will match. That's why only 9/163
enrichments were ever reused — the terms are too specific.

### P2: Confidence never updates (always 0.50)
The `learn()` stores enrichments with confidence=0.50 always.
There's no mechanism to increase confidence when an enrichment is reused and
the retrieval succeeds, or decrease it when it fails.

### P3: Chunk gates are NOT applied post-retrieval
The `batch_chunk_gates()` method exists but is never called in the agent.
The Kalman-updated chunk memories (940 entries) are being stored but
never used to re-rank results. This is the GAM-RAG paper's main contribution
and we're not using it.

### P4: Strategy memory is passive
Strategies track which tools work for which query types, but the agent
never consults `get_strategy()` to decide which tool to call first.
The LLM decides independently every time.

### P5: Regressions are LLM variance, not RecallDB harm
The 3 regressions (gs4-029, gs4-031, gs4-065) show different tool call
patterns but no evidence that enrichments hurt. With temp=0.0 the LLM
should be deterministic, but OpenRouter routing + prompt sensitivity
still causes ~5pp variance.

### P6: Enrichments are too noisy — every GDPR article gets dumped in
Top-3 results may cite 10+ GDPR articles. All get stored as expansions.
A query about "data deletion" gets enriched with "Article 5(1)(c), Article 6(1),
Article 12, Article 13, Article 14, Article 15, Article 17, Article 32..."
This dilutes the embedding instead of sharpening it.

## 6. IMPROVEMENT OPPORTUNITIES

### Fix 1: Atomic term extraction (HIGH IMPACT, EASY)
Instead of storing full query as enrichment term:
- Extract entities: DPA names, company names, GDPR article groups
- Store each as separate enrichment with focused expansions
- Example: query "TikTok fine Dutch DPA" →
  - enrichment("TikTok", ["TikTok Technology Limited", "ByteDance"])
  - enrichment("Dutch DPA", ["Autoriteit Persoonsgegevens", "AP", "Netherlands"])

### Fix 2: Apply chunk gates post-retrieval (HIGH IMPACT, MEDIUM)
After RRF fusion, before cross-encoder reranking:
```python
# Apply RecallDB chunk gates to RRF scores
if recalldb_memory:
    gates = recalldb_memory.batch_chunk_gates(top_child_ids, query_vec)
    rrf_scores = {cid: score * gates[cid] for cid, score in rrf_scores.items()}
```
This would suppress chunks that consistently fail and boost reliable ones.
With 201 high-confidence chunks, this could shift MRR significantly.

### Fix 3: Confidence feedback loop (MEDIUM IMPACT, EASY)
After retrieval, if enrichments were used and retrieval succeeded:
```python
if enrichments_used and relevance_score > 0.6:
    for e in enrichments_used:
        store.update_confidence(e.id, min(e.confidence + 0.1, 1.0))
        store.increment_hits(e.id)
```
Failed retrievals should decay confidence. This creates natural selection
of good enrichments.

### Fix 4: Top-K expansion filtering (MEDIUM IMPACT, EASY)
Instead of dumping all expansions into the query:
- Only inject top-3 most relevant expansions (by confidence * frequency)
- Weight by inverse document frequency (rare terms help more)
- Never inject more than 5 expansion tokens (prevents embedding dilution)

### Fix 5: Strategy-guided tool selection (MEDIUM IMPACT, MEDIUM)
Inject strategy into agent prompt:
```python
strategy = recalldb_memory.get_strategy(query_type)
if strategy and strategy.avg_relevance > 0.7:
    hint = f"Recommended tools: {strategy.tool_sequence} (avg_relevance={strategy.avg_relevance:.2f})"
    # Prepend to system prompt or add as tool result
```

### Fix 6: Self-answer search depth (from AutoSearch paper) (HIGH IMPACT, HARD)
After each tool call, generate a brief self-answer. If confidence is high
enough (measured by answer overlap with query entities), stop searching.
This directly targets tool call reduction:
- Current: agent always uses 2-6 tools
- Target: stop at 1-2 if first search is already sufficient

### Fix 7: Correctness-gated enrichment (from ERM paper) (HIGH IMPACT, MEDIUM)
Only store enrichments when retrieval clearly succeeded:
```python
if avg_cross_encoder_score > 0.7:  # high confidence retrieval
    store_enrichments(...)
elif avg_cross_encoder_score < 0.3:  # clearly failed
    decay_similar_enrichments(...)
```
This prevents noisy/wrong enrichments from accumulating.

## 7. PRIORITY ORDER (effort vs impact)

```
Priority  Fix                        Impact    Effort   Expected Gain
───────────────────────────────────────────────────────────────────────
1         Chunk gates (Fix 2)        HIGH      2h       MRR +10-15%
2         Atomic terms (Fix 1)       HIGH      3h       Hit rate +5-10%
3         Top-K expansion (Fix 4)    MEDIUM    1h       MRR +5%
4         Confidence loop (Fix 3)    MEDIUM    1h       Long-term stability
5         Correctness gate (Fix 7)   HIGH      2h       Prevents drift
6         Strategy hints (Fix 5)     MEDIUM    2h       Tools -15-20%
7         Self-answer depth (Fix 6)  HIGH      4h       Tools -30-40%
```

## 8. RESEARCH REFERENCES

- GAM-RAG (ICML 2026): Kalman chunk memory — we implement but don't USE the gates
- ERM / RAG without Forgetting (Feb 2026): Correctness-gated enrichment updates
- AutoSearch (ACL Findings 2026): Self-answer RL for adaptive search depth
- HiPRAG (2025): Hierarchical rewards to reduce over-search from 27% to 2.3%
- MARAG-R1 (2025): RL-trained multi-tool retrieval agent
