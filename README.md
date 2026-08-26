# GDPRScope

GDPR enforcement research tool. Searches 8,000+ real enforcement decisions across 36 jurisdictions to answer questions about fines, precedents, and regulatory patterns.

Built for privacy lawyers and DPOs who spend hours manually researching enforcement history.

## How it works

A LangGraph ReAct agent with 9 tools queries a PostgreSQL database of GDPR enforcement decisions. The retrieval pipeline combines dense vectors, sparse lexical weights, BM25 full-text search, and cross-encoder reranking via Reciprocal Rank Fusion.

The agent decomposes complex queries into parallel sub-searches, applies HyDE (Hypothetical Document Embeddings) for conceptual queries, and uses intent extraction to route to specialized search arms.

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
  |-> intent extraction (LLM)
  |-> query type classification (conceptual / entity / article / scenario / ...)
  |-> HyDE embedding (generates hypothetical decision excerpt, then embeds)
  |
  |-> dense vector search (BGE-M3, 1024d, section-aware routing)
  |-> BM25 full-text search (tsvector, with article text boost)
  |-> sparse lexical search (BGE-M3 learned token weights, JSONB)
  |-> fine-sort arm, case-number lookup, case-factors arm (conditional)
  |
  |-> Reciprocal Rank Fusion (all arms merged)
  |-> cross-encoder reranking (bge-reranker-v2-m3, for conceptual queries)
  |-> parent chunk expansion (child retrieval -> parent context to LLM)
```

### Fine simulator

Implements the EDPB 5-step methodology against real precedent data:

1. Categorize violation severity (Art. 83(4) vs 83(5-6))
2. Calculate starting point from turnover and severity band
3. Find matching precedents with cascading relaxation (articles + jurisdiction + sector -> articles only)
4. Evaluate Art. 83(2) factors (cooperation, intent, sensitive data, prior violations)
5. Produce weighted percentile range (P25-P75) with confidence score

Similarity scoring uses eta-squared variable importance measured on 3,841 fined cases: jurisdiction 21.6%, sector 18.4%, article 14.4%.

## Eval results

Evaluated on a golden set of 416 queries across 9 categories (named_entity, conceptual, scenario, fine_lookup, false_premise, cross_jurisdiction, edge_case, article_lookup, multi_target).

Metric: Hit Rate @ 5 (does the expected document appear in the top 5 retrieved?).

```
Single-query RAG baseline:     ~50%  HR@5
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

Weakest category (conceptual) is bounded by embedding distance between abstract legal questions and specific decision text. Many "misses" retrieve equally valid alternative precedents not in the golden set.

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
  rag.py                Hybrid retrieval engine (1,908 lines)

services/
  agent.py              LangGraph ReAct agent, 9 tools (1,541 lines)
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
  test_fine_simulator.py  55 tests (categorization, starting point, percentiles, factor analysis, leave-one-out)
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
