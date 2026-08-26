# GDPRScope

GDPR enforcement research tool. Searches 8,000+ real enforcement decisions across 36 jurisdictions to answer questions about fines, precedents, and regulatory patterns.

Built for privacy lawyers and DPOs who need to research enforcement precedents across multiple DPAs and jurisdictions — a process that typically involves searching GDPRhub, EUR-Lex, and individual DPA websites separately.

## How it works

A [LangGraph](https://langchain-ai.github.io/langgraph/) ReAct agent (Reason + Act: the LLM decides which tools to call, reads the results, and decides whether to search again or answer) with 9 tools queries a PostgreSQL database of GDPR enforcement decisions.

The retrieval pipeline combines multiple search strategies and merges them via [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~grbovic/p131-cormack.pdf) (RRF — a method that merges ranked lists from different search methods by combining their positions, so a document ranked high by multiple methods rises to the top):

- **Dense vector search** — each text is encoded as 1024 numbers (an "embedding") that capture meaning; similar texts have similar numbers, so searching is finding the closest vectors
- **Sparse lexical search** — each text produces ~50-100 (word: importance weight) pairs, learned by the model; matches exact terms but with learned importance rather than raw frequency
- **BM25 full-text search** — classic keyword matching via PostgreSQL tsvector; finds documents that contain the query terms regardless of meaning
- **Cross-encoder reranking** — a second model that reads (query + document) together and scores relevance; more accurate than comparing vectors separately but slower, so only applied to the top ~20 candidates

The agent decomposes complex queries into parallel sub-searches, applies HyDE (generates a hypothetical decision excerpt and embeds that instead of the raw question, bridging the vocabulary gap between questions and legal text), and uses intent extraction to route to specialized search arms.

### Agent tools

| Tool | What it does |
|---|---|
| `search_precedents` | Full RAG pipeline: intent extraction, HyDE, section-aware vector search, RRF fusion, cross-encoder reranking |
| `search_by_article` | Find decisions by GDPR article number with semantic reranking. Sub-article precision (e.g., "6(1)(f)") |
| `search_by_entity` | Find decisions by company/authority name with fuzzy matching |
| `simulate_fine` | EDPB 5-step fine estimation from matching precedents (P25-P75 range, not a point estimate) |
| `lookup_law` | Retrieve GDPR article text + relevant recitals |
| `analyze_factors` | Art. 83(2) aggravating/mitigating factor analysis from case_factors table |
| `dpa_profile` | DPA behavioral profile: median fine, trend, enforcement volume |
| `read_memory` / `write_memory` | Persistent user context across sessions |

### Retrieval pipeline (inside `search_precedents`)

```
query
  |-> intent extraction (LLM parses entity names, articles, jurisdiction from the question)
  |-> query type classification (conceptual / entity / article / scenario / ...)
  |-> HyDE embedding (LLM writes a fake decision excerpt, then embeds that instead of the question)
  |
  |-> dense vector search (BGE-M3, 1024 dims, section-aware routing)
  |-> BM25 full-text search (PostgreSQL tsvector, with article title boost)
  |-> sparse lexical search (BGE-M3 learned token weights, stored as JSONB)
  |-> fine-sort arm, case-number lookup, case-factors arm (conditional)
  |
  |-> Reciprocal Rank Fusion (merges all ranked lists by position)
  |-> cross-encoder reranking (reads query+doc together, for conceptual queries)
  |-> parent chunk expansion (small chunks for search -> full section sent to LLM)
```

### Fine simulator

Implements the [EDPB 5-step methodology](https://www.edpb.europa.eu/system/files/2023-05/edpb_guidelines_042022_calculationofadministrativefines_en.pdf) against real precedent data:

1. Categorize violation severity (Art. 83(4) vs 83(5-6))
2. Calculate starting point from turnover and severity band
3. Find matching precedents with cascading relaxation (articles + jurisdiction + sector -> articles only)
4. Evaluate Art. 83(2) factors (cooperation, intent, sensitive data, prior violations)
5. Produce weighted percentile range (P25-P75) with confidence score

Similarity scoring uses eta-squared variable importance measured on 3,841 fined cases: jurisdiction 21.6%, sector 18.4%, article 14.4%.

## Eval results

Evaluated on a golden set of 416 queries across 9 categories.

Metric: Hit Rate @ 5 — does the expected document appear in the top 5 retrieved?

```
Single-query vector search:    ~50%  HR@5
Agent (multi-turn, 9 tools):    82%  HR@5
```

Per-category breakdown (agent):

| Category | HR@5 | Queries |
|---|---|---|
| cross_jurisdiction | 97% | 40 |
| fine_lookup | 92% | 50 |
| false_premise | 90% | 50 |
| named_entity | 88% | 80 |
| edge_case | 84% | 37 |
| scenario | 81% | 50 |
| multi_target | 77% | 25 |
| article_lookup | 76% | 34 |
| conceptual | 64% | 50 |

The weakest category (conceptual, 64%) is limited by the embedding model — BGE-M3 is a general-purpose multilingual model, not specialized in legal text. Abstract legal questions like "Can a DPO also be the IT manager?" produce embeddings that are distant from the specific decision text that answers them. A legal-domain embedding model or fine-tuning BGE-M3 on (GDPR query, relevant decision) pairs would likely improve these categories, but was out of scope. Many "misses" in this category do retrieve relevant precedents — just not the specific one in the golden set.

## Data sources

| Source | Records | Access |
|---|---|---|
| GDPR Enforcement Tracker | 3,202 cases | HTML scraping (JSON embedded in page) |
| GDPRhub | 3,549 decisions | MediaWiki API (CC-BY-SA) |
| EDPB one-stop-shop decisions | 1,326 | Public API |

Total: 8,077 documents, 98,569 chunks (parent-child pattern), 84,874 with sparse embeddings.

## Project structure

```
db/
  schema.sql            DDL: documents, chunks, citations, user_memory, research_sessions
  ingest.py             Multi-source ingestion (tracker, gdprhub, eurlex). Idempotent.
  ingest_edpb.py        EDPB one-stop-shop ingestion
  ingest_gdpr_law.py    GDPR articles + recitals
  ingest_noyb.py        noyb complaint ingestion
  chunker.py            Parent-child chunking (section-aware, sliding window)
  embed_bge_m3.py       Dense embeddings (BGE-M3, 1024d, local GPU)
  embed_sparse.py       Sparse lexical embeddings (BGE-M3 sparse_linear head)
  enrich.py             Document enrichment pipeline
  enrich_legal.py       Legal metadata extraction
  enrich_sector.py      Sector classification
  extract_factors.py    Art. 83(2) factor extraction (LLM-based)
  rag.py                Hybrid retrieval engine

services/
  agent.py              LangGraph ReAct agent, 9 tools
  fine_simulator.py     EDPB 5-step fine estimation engine
  dpa_profiles.py       DPA behavioral profile generation
  intelligence.py       Enforcement trend analytics
  memory.py             Persistent user memory (cross-session)

ui/
  app.py                Streamlit entry point
  views/                8 tabs: research, analyzer, search, trends, compare, intelligence, my_dpa, case_detail

eval/
  eval_agent.py         Agent evaluation pipeline (HR@K, MRR, tool error detection)
  golden_set_v5.json    416 queries, 9 categories

tests/
  test_fine_simulator.py  55 tests (categorization, starting point, percentiles, factor analysis)
```

## Stack

| Component | Technology |
|---|---|
| Database | PostgreSQL 16 + pgvector (dev) / CockroachDB Serverless (prod) |
| Dense embeddings | BGE-M3 (1024d, local GPU via sentence-transformers) |
| Sparse embeddings | BGE-M3 lexical weights (sparse_linear.pt head, stored as JSONB) |
| Cross-encoder reranker | bge-reranker-v2-m3 |
| Agent framework | LangGraph (ReAct pattern) |
| LLM (generation) | Claude Sonnet via Anthropic API |
| LLM (intent/HyDE) | Kimi K2 via OpenRouter |
| UI | Streamlit |
| Ingestion | psycopg3 direct, requests, MediaWiki API, SPARQL |

## Design decisions

Key architectural choices and the reasoning behind them.

**Why hybrid retrieval (dense + sparse + BM25) instead of just vector search?**
Single-mode vector search hit ~50% HR@5 on our eval set. Dense embeddings capture meaning but miss exact terms (entity names, article numbers). BM25 catches exact matches but misses semantic similarity. Sparse learned weights (BGE-M3 lexical head) sit between both — they match terms but with model-learned importance. [RRF](https://plg.uwaterloo.ca/~grbovic/p131-cormack.pdf) merges their ranked lists so a document found by multiple methods ranks higher. We tested sparse as a post-RRF override (v11, 81.2%) vs as an RRF arm (v12, 81.8%) — the override approach destroyed good rankings from the other arms.

**Why parent-child chunking instead of fixed-size chunks?**
Legal decisions have natural sections (facts, holding, dispute). Small chunks (~625 tokens) are better for retrieval precision — the embedding captures a focused idea. But the LLM needs the full section to reason correctly. Parent-child solves this: search against children, send parents to the LLM. This pattern is [well-documented in production RAG systems](https://docs.llamaindex.ai/en/stable/optimizing/production_rag/).

**Why HyDE (Hypothetical Document Embeddings)?**
A user asks "Can a DPO also be the IT manager?" but decisions are written as "The DPA found that the controller violated Art. 38(6) by appointing a DPO who also held the position of IT director...". The question and the answer use different vocabulary. [HyDE](https://arxiv.org/abs/2212.10496) bridges this by generating a hypothetical decision excerpt from the question and embedding that instead. Measured improvement: +14pp on article_lookup queries.

**Why BGE-M3 instead of a legal-domain embedding model?**
The [Massive Legal Embedding Benchmark (MLEB)](https://arxiv.org/abs/2510.19365) shows legal-specialized models (Voyage Law-2, Kanon 2) outperform general models on GDPR retrieval tasks. BGE-M3 was chosen because it provides dense + sparse embeddings from a single model (reducing infrastructure complexity) and runs locally on GPU without API costs. A legal-domain model or fine-tuning on (GDPR query, decision) pairs is the most direct path to improving the 64% HR@5 on conceptual queries.

**Why an agent instead of a single RAG pipeline?**
Single-query RAG fails on three patterns: superlative queries ("highest fine for Art. 32"), entity-specific queries (large corpus = entity gets buried), and multi-document synthesis ("typical fine range in France"). The agent can call `search_by_entity` for exact matches, `search_by_article` with filters for article-specific queries, and `simulate_fine` for range questions — choosing the right tool per query type. This lifted HR@5 from ~50% to 82%.

**Why EDPB 5-step methodology for fine simulation?**
The [EDPB Guidelines 04/2022](https://www.edpb.europa.eu/system/files/2023-05/edpb_guidelines_042022_calculationofadministrativefines_en.pdf) define how DPAs should calculate fines. Using the same methodology as the regulators (severity classification, turnover-based starting point, Art. 83(2) factor adjustment) produces ranges grounded in how fines are actually determined, not statistical averages that ignore legal structure. Variable importance was measured empirically via eta-squared on 3,841 fined cases.

**What was considered and discarded?**
- *ColBERT (multi-vector retrieval)*: 28 GB storage for 85K chunks, redundant with cross-encoder reranking (both do token-level comparison, but cross-encoder does full cross-attention which is strictly more powerful than ColBERT's MaxSim).
- *GraphRAG*: Researched [LegalGraphRAG](https://arxiv.org/abs/2605.28120) (fact graphs + ontology communities) and [Graph RAG for Legal Norms](https://arxiv.org/abs/2505.00039) (versioned legislation graphs). The citations table exists in the schema for future graph traversal, but the agentic approach with structured tools achieved 82% HR@5 without graph infrastructure.
- *RecallDB (retrieval learning)*: Hooks exist in the code but disabled — the agent's tool usage pattern is too consistent for retrieval learning to provide meaningful signal.

## Setup

```bash
# Clone and install
pip install -r requirements.txt

# Configure (copy .env.example, fill in DATABASE_URL, API keys)
cp .env.example .env

# Load environment
export $(grep -v '^#' .env | xargs)

# Run ingestion pipeline (idempotent, safe to re-run)
PYTHONUTF8=1 python db/ingest.py --source all
PYTHONUTF8=1 python db/embed_bge_m3.py
PYTHONUTF8=1 python db/embed_sparse.py

# Run tests
PYTHONUTF8=1 python -m pytest tests/ -v

# Start UI
PYTHONUTF8=1 streamlit run ui/app.py --server.port 8501
```

Requires a PostgreSQL database with pgvector extension and a GPU for local embedding generation.

## License

Not yet licensed. All GDPR enforcement data sourced from public records. GDPRhub content is CC-BY-SA.
