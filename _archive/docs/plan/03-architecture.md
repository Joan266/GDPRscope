# 03 — Arquitectura

**Ultima actualizacion: 2026-08-11 (schema v2 aplicado)**

## Stack

| Capa | Tecnologia | Estado |
|---|---|---|
| DB desarrollo | PostgreSQL 16 + pgvector (Docker local) | ACTIVO |
| DB demo | CockroachDB Serverless (cuenta nueva) | PENDIENTE |
| Embeddings | e5-large-v2 (sentence-transformers, local) | COMPLETADO (68K chunks) |
| LLM enrichment | Nova Micro via OpenRouter | COMPLETADO ($0.08) |
| LLM profile extraction | Claude Haiku via Anthropic API | PENDIENTE |
| UI | Streamlit | ACTIVO (localhost:8501) |
| Scraper | requests + BeautifulSoup | PENDIENTE |

### Decisiones de stack

- **Bedrock**: BLOQUEADO. No usar.
- **OpenRouter**: $4.92 credito restante. Nova Micro para extraction.
- **Anthropic API**: $10 credito. Haiku para profile extraction de privacy policies.
- **FastAPI**: No necesario — Streamlit hace queries SQL directas.

## Flujo de datos

```
Fuentes externas                    Pipeline                          DB
-------------------                 --------                          --
Enforcement Tracker  --JSON local--> ingest.py ----> documents (3,202)
GDPRhub API          --MediaWiki---> ingest.py ----> documents + chunks (3,549)
GitHub GDPR text     --HTTP--------> ingest_gdpr_law.py -> gdpr_law (99) + gdpr_recitals (173)
Noyb cases           --HTML-------> ingest_noyb.py ----> noyb_complaints (~900) [NUEVO]
DPcuria CJEU         --HTML-------> ingest_dpcuria.py -> documents (source='dpcuria', ~181) [NUEVO]
AEPD PDFs            --HEAD probe-> probe_aepd.py -----> aepd_pipeline (1,388 staging) [NUEVO]
AEPD RSS             --XML--------> ingest_aepd_rss.py -> documents (monitor) [NUEVO]
                                     |
                                     v
                              enrich_legal.py ----> violation_type (3,335)
                              extract_factors.py -> case_factors (765) [OpenRouter Nova Micro]
                              link_sources.py ----> canonical_id (363)
                              enrich_sector.py ---> sector backfill (105)
                                     |
                                     v
                                  embed.py ----> chunks.embedding (e5-large-v2, 68K done)
```

## Flujo de query — NUEVO (con memoria)

```
Usuario llega por primera vez
    |
    v
[Opcion A] Pega URL de su empresa
    |
    v
scrape_privacy_policy(url)
    → requests GET → extract text from /privacy-policy (o variantes)
    → LLM (Haiku) extrae: jurisdiction, sector, data_types, legal_bases, transfers
    → INSERT INTO user_memory (user_id, memory_key='org_profile', memory_value=JSONB)
    |
    v
[Opcion B] Usa sin registro (anonimo)
    → Funcionalidad completa pero sin personalizacion
    → Si vuelve y se registra, research_sessions previas se vinculan
    |
    v
Tab selector (rediseñado)
    |
    +-- "Analyzer"    --> fine_simulator.py → contextualizado con org_profile si existe
    +-- "Search"      --> SQL filtrado → pre-filtrado por jurisdiccion/sector del perfil
    +-- "My DPA"      --> DPA behavioral profile de TU jurisdiccion (auto-detectada)
    +-- "Compare"     --> SQL agrupado por jurisdiccion → tabla comparativa
    +-- "Trends"      --> SQL time series → graficos de lineas
    +-- "Memory"      --> research_sessions + "novedades desde tu ultima visita"
    |
    v
Post-query:
    → INSERT INTO research_sessions (query, filters, results_count, ...)
    → Si org_profile existe: match nueva decision vs perfil → flag relevancia
    |
    v
Streamlit renderiza (HTML + charts)
```

## Componentes nuevos (por implementar)

### Privacy Policy Scraper
```python
# services/profile_scraper.py (~100 lineas)
async def scrape_privacy_policy(url: str) -> str:
    """Fetch and extract text from privacy policy page."""
    # Try common paths: /privacy-policy, /privacy, /legal/privacy
    # Extract main content, strip nav/footer
    # Return clean text

async def extract_org_profile(policy_text: str) -> OrgProfile:
    """Use LLM to extract structured profile from privacy policy text."""
    # Prompt Haiku with policy text
    # Return: jurisdiction, sector, data_types[], legal_bases[], transfers[]
```

### Research Memory Engine
```python
# services/memory.py (~80 lineas)
def save_research(user_id, query, filters, results_summary):
    """Save research session to CockroachDB."""

def get_updates_since(user_id, last_visit) -> list[dict]:
    """Find new decisions matching user's past research patterns."""
    # Match: jurisdiction + articles from past queries
    # Return decisions added since last_visit
```

### DPA Profile Generator
```python
# services/dpa_profiles.py (~120 lineas)
def generate_dpa_profile(jurisdiction: str) -> DPAProfile:
    """Generate behavioral profile of a DPA from enforcement data."""
    # Total fines, median, max
    # Top articles enforced (with %)
    # Top sectors targeted
    # Temporal trend (increasing/decreasing)
    # Cooperation credit (from case_factors)
    # Comparison vs other DPAs
```

## Schema DB

### Tablas principales

```sql
documents (id, source, title, jurisdiction, gdpr_articles, fine_amount, sector, ..., source_metadata JSONB)
-- source_metadata: campos especificos por fuente (dpcuria: procedural_type, legal_category; aepd: expediente, concepts)
-- source values: 'gdprhub' | 'eurlex' | 'enforcement_tracker' | 'dpcuria' | 'aepd'
chunks (id, document_id, chunk_type, section, content, embedding, ...)
case_factors (id, document_id, factor_a_gravity, ..., factor_h_discovery, ...)
gdpr_law (article_number, title, chapter, content)
gdpr_recitals (recital_number, content)
```

### Tablas nuevas (schema v2 — Canonical + Medallion)

```sql
noyb_complaints (id, case_id, controller, dpa_name, dpa_country, status, duration_days, document_id FK)
-- Entidad distinta (queja, no decision). FK a documents si la queja resulto en decision.

aepd_pipeline (id, expediente, year, number, pdf_url, raw_text, pipeline_status, document_id FK)
-- Staging table (Bronze layer). Cuando se promueve a 'promoted', crea fila en documents (Silver).

data_sources_sync (source_name PK, last_sync_at, last_sync_status, records_total, records_new)
-- Control operacional de sincronizacion por fuente.
```

### Diseno del schema v2 (justificacion)

Basado en investigacion de buenas practicas (Canonical Data Model, Medallion Architecture, Entity Resolution):
- **documents = capa Silver canonica** — todas las decisiones/sentencias van aqui, independientemente de la fuente
- **DPcuria CJEU → documents directamente** (source='dpcuria'), campos extra en source_metadata JSONB
- **AEPD → aepd_pipeline como staging (Bronze)** → se promueve a documents cuando esta procesado
- **noyb → tabla separada** porque son quejas, no decisiones (entidad distinta)
- **source_metadata JSONB** evita column bloat: campos por fuente sin anadir columnas huerfanas

### Tablas de memoria (existentes, ahora centrales — con temporal decay)

```sql
user_memory (
    user_id TEXT,
    memory_key TEXT,           -- 'org_profile' | 'preferences' | 'research_context'
    memory_value JSONB,        -- perfil organizacional, preferencias, contexto
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    last_accessed_at TIMESTAMPTZ,  -- NUEVO: temporal decay (patron Mem0)
    access_count INTEGER DEFAULT 0 -- NUEVO: frecuencia de uso
)

research_sessions (
    id UUID PK,
    user_id TEXT,
    query TEXT,                -- query original del usuario
    intent TEXT,               -- 'simulate' | 'search' | 'benchmark' | 'compare'
    filters JSONB,             -- {jurisdiction, articles, sector, ...}
    results_summary JSONB,     -- {count, top_result, range, ...}
    created_at TIMESTAMPTZ
)
```

### org_profile JSONB structure

```json
{
    "company_url": "https://example.com",
    "jurisdiction": "Germany",
    "sector": "Finance",
    "data_types": ["financial", "identification", "contact"],
    "legal_bases": ["consent", "legitimate_interest", "contract"],
    "transfers": [{"destination": "US", "mechanism": "SCCs"}],
    "special_categories": false,
    "extracted_at": "2026-08-15T10:30:00Z",
    "confirmed_by_user": true
}
```

## Volumenes actuales (2026-08-11)

| Tabla | Registros | Post-ingesta (estimado) |
|---|---|---|
| documents (+source_metadata) | 6,751 | ~6,932 (+181 CJEU via dpcuria) |
| chunks | 309,195 (68,225 con embeddings) | ~315,000 |
| case_factors | 765 | 765 (sin cambio) |
| gdpr_law | 99 | 99 |
| gdpr_recitals | 173 | 173 |
| violation_type enriched | 3,335 | 3,335 |
| canonical_id linked | 363 | ~400 |
| noyb_complaints | 0 | ~900 |
| aepd_pipeline | 0 | ~1,388 (staging) |
| data_sources_sync | 6 | 6 |

## Migracion a CockroachDB (dia 6)

1. Crear cuenta NUEVA (free tier: 50M RUs)
2. Adaptar indexes:
   - GIN → INVERTED
   - HNSW → CREATE VECTOR INDEX con prefix partitioning por user_id (C-SPANN)
3. Exportar datos de Docker local → importar a CockroachDB
4. Verificar: user_memory + research_sessions + vector search funcionan
5. Habilitar Row-Level Security en user_memory (multi-tenant)
6. CockroachDB tools para hackathon (4 requeridos, necesitamos 2+):
   - **Vector Indexing** (C-SPANN) — embeddings + semantic search
   - **Managed MCP Server** — agente inspecciona DB en vivo (copy-paste config, ~1h)
   - **Agent Skills** — auto-diagnostico de DB (install one-liner, ~1h)
   - **ccloud CLI** — setup/deployment automatizado
7. AWS services para hackathon (1+ requerido):
   - **Bedrock Claude Sonnet** — LLM para RAG + memory extraction
   - **Bedrock Guardrails** — Automated Reasoning + Contextual Grounding (~3-4h)

## Convenciones de codigo

- Type hints en todas las funciones
- SQL 100% parametrizado (%s), nunca f-strings con datos
- Idempotencia: ON CONFLICT DO UPDATE
- logging con `log = logging.getLogger(__name__)`, no print()
- psycopg3 directo, no ORM
- DATABASE_URL desde os.environ, nunca hardcodeada
- Archivos < 400 lineas, funciones < 50 lineas
- `PYTHONUTF8=1` siempre en Windows
- e5-large-v2: prefijo `"passage: "` en docs, `"query: "` en queries
