# 07 — Hackathon (CockroachDB x AWS)

Deadline: 18 agosto 2026

**Ultima actualizacion: 2026-08-11**

## Narrativa central: "The GDPR Agent That Knows You"

Un agente GDPR con memoria persistente que:

1. **Te conoce** — scrappea tu privacy policy y construye tu perfil de riesgo en 30 segundos
2. **Conoce a tu regulador** — perfiles de comportamiento de 36 DPAs basados en 6,751 decisiones reales
3. **Te recuerda** — cada consulta se acumula, vuelves y el agente sabe donde lo dejaste
4. **Nunca se cae** — memoria sobre CockroachDB que sobrevive a fallos, regiones, y cambios de personal

## Por que CockroachDB ES el producto

| Concepto hackathon | Implementacion JurisMind |
|---|---|
| **Persistent memory** | org_profile + research_sessions + user_memory en CockroachDB |
| **Memory that survives failures** | Demo: matar nodo → la investigacion del DPO persiste |
| **Distributed Vector Indexing** | 68K embeddings para semantic search de precedentes |
| **Memory that grows** | Cada nueva decision DPA se ingesta → agente "sabe mas" |
| **MCP Server** | Agente conectado directamente al cluster CockroachDB |
| **Production-grade** | EU data sovereignty: datos GDPR en DB multi-region EU |

## Requisitos del hackathon — checklist

### CockroachDB tools (minimo 2):
- [x] **Distributed Vector Indexing** — 68K embeddings para RAG/similarity
- [ ] **MCP Server** — conexion directa agente → cluster
- [ ] **ccloud CLI** — provisioning del cluster (opcional extra)

### AWS services (minimo 1):
- [ ] **Amazon Bedrock** — Claude Haiku para profile extraction
- [ ] O alternativamente: Anthropic API directa (misma familia de modelos)

## Demo — Video de 3 min

### Escena 1: Hook (15 seg)
"500,000 DPOs in Europe. They all face the same question: how much will the fine be?
Today they spend 2-4 hours searching precedents manually. JurisMind answers in 30 seconds.
But here's what's different: it remembers."

### Escena 2: Auto-Profile (30 seg)
- Pego la URL de una empresa
- El agente scrappea la privacy policy
- En 30 segundos: "You're a fintech in Germany, processing financial and health data
  under legitimate interest, with transfers to AWS US via SCCs. Correct?"
- Confirmo → perfil guardado en CockroachDB

### Escena 3: Enforcement Analysis (45 seg)
- "What's my fine exposure for an Art. 32 data breach?"
- El agente ya sabe mi jurisdiccion (BfDI), mi sector (fintech), mis datos
- Range bar: P25-P75 con confidence badge "High — 47 similar cases"
- Top 5 precedentes con similarity score
- Factor impacts: "Cooperation reduces fines by 15% based on 243 cases"
- DPA comparison: "In Germany EUR 90K median. In France it would be EUR 250K."

### Escena 4: Memory Demo (30 seg)
- Cierro el navegador
- Abro de nuevo al dia siguiente
- El agente dice: "Since your last visit, the BfDI published a new decision on Art. 32
  in the financial sector. It's relevant to your profile. Fine: EUR 120,000.
  Want to see how this changes your exposure range?"
- ESTA es la memoria persistente. ESTO es lo que nadie ofrece.

### Escena 5: DPA Intelligence (20 seg)
- Tab "My DPA" → perfil completo del BfDI
- "The BfDI fines 40% less than the CNIL for the same violation type"
- Trend: "Enforcement increased 18% year-over-year"
- Top articles: Art. 32 (25%), Art. 5 (20%), Art. 6 (18%)

### Escena 6: Architecture + CockroachDB (15 seg)
- Diagrama: User → Streamlit → CockroachDB (persistent memory) + Vector Search
- "6,751 enforcement decisions. 765 structured Art. 83 factors. 68,000 embeddings.
  All in CockroachDB. Memory that never goes down."

### Escena 7: Close (15 seg)
- "JurisMind: the GDPR agent that knows you, knows your regulator, and remembers.
  Free. Open source. Built on CockroachDB."

## Criterios de los jueces — como ganamos

### 1. Agentic Memory Design (target: 8/10)
- **org_profile**: el agente conoce tu empresa sin que le cuentes secretos
- **research_sessions**: recuerda cada consulta, construye contexto acumulativo
- **updates_since**: cruza nuevas decisiones con tu perfil y tu historial
- **user_memory**: preferencias, ajustes, contexto persistente
- **CockroachDB no es "la base de datos" — ES la memoria del agente**

### 2. Technical Implementation (target: 7/10)
- **Vector Indexing**: 68K embeddings para similarity search de precedentes
- **MCP Server**: agente conectado directamente al cluster
- **Structured data + vectors**: hybrid search (SQL + semantic)
- **Calibrated engine**: eta-squared weighted similarity, backtest validated

### 3. Real-World Impact (target: 9/10)
- **500K+ DPOs** en la UE que necesitan esto
- **Gratuito** → maximo alcance
- **Basado en 6,751 decisiones reales** — no teoricas
- **Reduce 2-4 horas a 30 segundos** — productividad 100x
- **Primer tool que combina enforcement data + memory + personalizacion**
- **Harvey AI cobra $100-2,000/mo** y no tiene esto

### 4. Production Readiness (target: 7/10)
- CockroachDB multi-region → EU data sovereignty (poetico para GDPR tool)
- Queries 100% parametrizadas (SQL injection impossible)
- Idempotent ingestion pipeline
- Error handling + graceful degradation
- Disclaimer legal siempre visible

### 5. Creativity & Originality (target: 8/10)
- **Privacy policy auto-profile** → nadie lo hace
- **DPA behavioral profiling** → nadie lo ofrece como analytics
- **Calibrated fine simulator** con 6,751 precedentes → GDPRFine tiene 64
- **"Memory that gets smarter"** → el agente mejora con cada sesion

## Riesgos hackathon

| Riesgo | Probabilidad | Mitigacion |
|---|---|---|
| Scraper falla en algunas URLs | Alta | Fallback: input manual (funcionalidad actual) |
| LLM extraction imprecisa | Media | Confirmacion del usuario + edicion manual |
| CockroachDB RUs se agotan | Media | Pre-computar agregados, batch inserts, cache |
| Tiempo insuficiente para UI | Media | Priorizar: Analyzer + Memory + My DPA. Rest is nice-to-have. |
| Bedrock sigue bloqueado | Alta | Anthropic API directa ($10 credito) |
| MCP Server setup complejo | Baja | Documentacion CockroachDB es clara |

## Coste estimado total

| Item | Coste |
|---|---|
| Factor extraction (765 docs) | $0.08 (ya gastado) |
| Profile extraction (~100 policies) | ~$0.50 (Haiku) |
| DB desarrollo | Docker local ($0) |
| DB demo | CockroachDB free tier ($0) |
| Embeddings | Local ($0) |
| Deploy | Streamlit Cloud free tier ($0) |
| **Total estimado** | **< $1.00** |
| **Credito disponible** | $4.92 OpenRouter + $10 Anthropic |
