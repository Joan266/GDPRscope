# 05 — Plan de Ejecucion

**Ultima actualizacion: 2026-08-11 (post-scout)**

Quedan 7 dias (11-18 ago). Plan reorganizado con hallazgos del scout 3-agente.

---

## FASES COMPLETADAS (dias 1-10)

### FASE 0: Infraestructura — COMPLETADA
- Docker PostgreSQL 16 + pgvector
- Schema v1 aplicado (5 tablas base)
- Schema v2 aplicado (08-11): source_metadata JSONB en documents, noyb_complaints, aepd_pipeline, data_sources_sync
- Diseno basado en Canonical Data Model + Medallion Architecture (investigacion buenas practicas)
- 1,388 AEPD PDF URLs descubiertas via probe (data/aepd_ps_urls.json)

### FASE 1: Datos base — COMPLETADA
- 6,751 documentos (Tracker + GDPRhub)
- 99 articulos GDPR + 173 recitales
- 765 factores Art. 83(2) extraidos
- 3,335 violation_type enriched
- 363 cross-source links
- 68,225 embeddings generados (e5-large-v2)

### FASE 3: Prosecution Simulator v2 — COMPLETADA
- EDPB 5-step methodology
- Eta-squared weighted similarity
- Recency weighting
- Weighted percentiles
- Confidence indicator
- Backtest: 81% within 1 OoM, 55% within 0.5 OoM

### FASE 5: UI v1 (Streamlit 7 tabs) — COMPLETADA
- Funcional pero sin memoria ni personalizacion

### FASE 4+6+7: Services + UI Redesign — COMPLETADA (08-11)
- `services/dpa_profiles.py` (364 lineas) — DPA behavioral profiles
- `services/profile_scraper.py` (245 lineas) — Privacy policy scraper + LLM extraction
- `services/fine_simulator.py` (614 lineas) — EDPB 5-step (ya existia)
- UI reescrita: monolito 830 lineas → 13 modulos (max 280 lineas)
- Arquitectura: `ui/app.py` (router) → `ui/views/` (tabs) → `ui/components/` (reutilizables)
- Paleta warm neutral (cream + navy), Lora serif headings, JetBrains Mono numbers
- XSS-safe (html.escape), SQL parametrizado, service layer pattern

---

## PLAN 7 DIAS RESTANTES (actualizado 08-11)

### DIA 1 (12 ago): DPA Profiles + Ingesta nuevas fuentes

**Morning: DPA Profiles service**
- [x] Crear `services/dpa_profiles.py` — COMPLETADO (364 lineas)
- [x] Test con AEPD (1,829 cases), datos verificados en UI

**Afternoon: Schema nuevas fuentes + Ingesta**
- [x] Investigacion buenas practicas: Canonical Data Model, Medallion, Entity Resolution
- [x] Schema v2 disenado y aplicado: source_metadata JSONB, noyb_complaints, aepd_pipeline, data_sources_sync
- [x] 1,388 AEPD PDF URLs probadas y guardadas en data/aepd_ps_urls.json
- [ ] Crear `db/ingest_noyb.py` (~80 lineas)
  - Scrapear https://noyb.eu/en/project/cases (45 paginas x 20 items) → noyb_complaints
- [ ] Crear `db/ingest_dpcuria.py` (~60 lineas)
  - ~181 decisiones CJEU → documents (source='dpcuria') + source_metadata JSONB

### DIA 2 (13 ago): Research Memory + Privacy Policy Scraper

**Morning: Memory service (Mem0-style lifecycle)**
- [ ] Crear `services/memory.py` (~100 lineas)
  - `save_research`, `get_research_history`, `get_updates_since`
  - `extract_and_store_facts`, `deduplicate_memories`
- [ ] Anadir columnas a user_memory: `last_accessed_at`, `access_count`

**Afternoon: Privacy Policy Scraper + LLM Profile**
- [x] Crear `services/profile_scraper.py` — COMPLETADO (245 lineas)
- [x] Integrado en UI (Smart Analysis expander en tab Analyzer)
- [ ] Test end-to-end con 5 URLs reales

### DIA 3 (14 ago): UI Rediseno — Enforcement Analyzer — COMPLETADO

- [x] Renombrar tabs: Analyzer, My DPA, Search, Compare, Trends, Case Detail
- [x] Tab Analyzer: URL input, range bar Zillow, confidence badge, factor tags, precedents, DPA comparison
- [x] Paleta warm neutral, serif headings (Lora), monospace numbers (JetBrains Mono)
- [x] Estructura modular: 13 archivos, ningun monolito

### DIA 4 (15 ago): UI — Memory + DPA Profile tabs — PARCIAL

**Tab My DPA — COMPLETADO**
- [x] DPA profile card con stats completos (total, median, max, trend)
- [x] Top articles enforced con porcentajes
- [x] Top sectors fined con porcentajes
- [x] Trend sparkline (line chart por ano)
- [x] Cooperation impact card con data backing
- [x] Intent split (intentional vs negligent)
- [x] Decisiones recientes (ultimas 5)
- [ ] Noyb cases pendientes contra esa DPA (requiere ingesta noyb)

**Tab Memory — PENDIENTE**
- [ ] Research timeline (consultas previas con timestamps)
- [ ] "Updates since last visit" panel
- [ ] Organization profile display/edit
- [ ] Memory stats

### DIA 5 (16 ago): Integracion + Polish + Bedrock Guardrails

- [ ] Contextualizar Search con org_profile (filtros pre-rellenados)
- [ ] Badge "Relevant to you" en resultados que matchean perfil
- [ ] Integrar Bedrock Guardrails (Automated Reasoning) para verificar respuestas
- [ ] Loading states, error handling, tooltips
- [ ] Disclaimer siempre visible
- [ ] Test end-to-end: URL → profile → simulate → search → memory → revisit
- [ ] Fix bugs

### DIA 6 (17 ago): Deploy CockroachDB + AWS

**Morning: Migracion CockroachDB**
- [ ] Crear cuenta nueva (free tier: 50M RUs)
- [ ] Adaptar schema: GIN → INVERTED, HNSW → CREATE VECTOR INDEX (C-SPANN)
- [ ] Habilitar prefix partitioning por user_id en vector index
- [ ] Export Docker → Import CockroachDB
- [ ] Verificar: user_memory + research_sessions + vector search funcionan
- [ ] Configurar Managed MCP Server (copy-paste desde Cloud Console, ~1h)
- [ ] Instalar Agent Skills desde cockroachdb-skills repo (~1h)

**Afternoon: Deploy**
- [ ] Streamlit Cloud o EC2 para UI
- [ ] Environment variables (DATABASE_URL, ANTHROPIC_API_KEY)
- [ ] Test funcional en produccion
- [ ] URL publica funcionando
- [ ] (Opcional) Exponer JurisMind como MCP Server via fastapi-mcp (~2-3h)

### DIA 7 (18 ago): Video + Submission

**Morning: Video de demo (3 min)**
- [ ] Grabar demo script (ver 07-hackathon.md)
- [ ] Demo moments clave:
  1. Pegar URL → perfil automatico → "esto es lo que sabemos de tu org"
  2. Simulador de multa → rango calibrado con precedentes
  3. "My DPA" → perfil de tu autoridad con comparativa
  4. "Updates since last visit" → "3 nuevas decisiones relevantes para ti"
  5. Noyb/CJEU data → "fuentes que nadie mas integra"
- [ ] Editar y subir a YouTube

**Afternoon: Submission en Devpost**
- [ ] Repo publico con README, LICENSE (MIT)
- [ ] URL demo funcional
- [ ] Video URL
- [ ] CockroachDB tools (4): Vector Indexing + MCP Server + Agent Skills + ccloud CLI
- [ ] AWS services (2): Bedrock Claude Sonnet + Bedrock Guardrails
- [ ] Diagrama arquitectura

---

## Lo que NO hacemos (scope control)

- LLM generando respuestas textuales (cero alucinaciones en datos)
- RAG clasico (retrieve + generate) — todo es SQL estructurado
- Auth complejo (hackathon: session ID simple)
- Mobile-specific UI
- Multi-idioma
- Alertas por email (solo in-app "updates since last visit")
- DPIA storage/management (eso es OneTrust territory)
- Scraping de PDFs de DPAs (mostramos links)
- FastAPI / API REST separada
