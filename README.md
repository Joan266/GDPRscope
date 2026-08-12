# GDPRScope — Enforcement Intelligence Platform

Statistical GDPR enforcement analysis powered by real decision data. Estimate fine exposure using the **EDPB 5-step methodology** against 6,751+ enforcement decisions across 36 jurisdictions.

## What it does

- **Fine Simulator** — EDPB 5-step methodology against real precedents (not theoretical maximums)
- **DPA Behavioral Profiles** — compare how different authorities enforce
- **Enforcement Trends** — time-series with jurisdiction/article/sector filters
- **Case Search** — hybrid search (keyword + vector) over GDPR decisions
- **Smart Analysis** — extract org profile from privacy policy (paste, upload, or URL)
- **Persistent Memory** — remembers your organization context across sessions

## Quick start

```bash
# Load environment
export $(grep -v '^#' .env | xargs)

# Start the app
PYTHONUTF8=1 streamlit run ui/app.py --server.port 8501
```

## Stack

| Layer | Technology |
|---|---|
| UI | Streamlit (modular tabs) |
| DB | PostgreSQL 16 + pgvector (Docker dev) / CockroachDB (prod) |
| Embeddings | e5-large-v2 local (1024 dims) |
| LLM | Claude Haiku (profile extraction) |
| Data | GDPRhub, GDPR Enforcement Tracker, EUR-Lex |

## Project structure

```
ui/
  app.py              — Entry point, header, tab routing
  styles.py           — Design tokens and CSS
  views/              — Tab modules (analyzer, trends, search, etc.)
  components/         — Reusable UI components
services/
  fine_simulator.py   — EDPB 5-step simulation engine
  dpa_profiles.py     — DPA behavioral profiles
  profile_scraper.py  — Privacy policy scraper + LLM extraction
  memory.py           — Persistent user memory
db/
  schema.sql          — DDL (canonical + medallion architecture)
  ingest.py           — Multi-source document ingestion
  embed.py            — Embedding generation (e5-large-v2)
  rag.py              — Hybrid retrieval + LLM
docs/plan/            — Hackathon plan and task tracking
```

## Data pipeline

```
1. ingest.py  → documents + chunks (idempotent)
2. embed.py   → embeddings for all chunks
3. rag.py     → hybrid retrieval + LLM answers
```

## Hackathon

CockroachDB x AWS — August 2026

---

*Based on real enforcement decisions — not theoretical maximums. Not legal advice.*
