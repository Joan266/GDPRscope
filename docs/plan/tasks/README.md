# GDPRScope — Plan Hackathon

**Hackathon: Agents for Humans (AWS/Strands) — Professional Agents track**
**Deadline: ~septiembre 2026 (~30 dias)**
**Pivot: CockroachDB → AWS (Strands Agents SDK + Bedrock + Aurora)**

## Que tenemos (DONE)

| # | Feature | Archivos clave |
|---|---|---|
| D1 | Simulador multas EDPB 5-step | `services/fine_simulator.py` + `ui/views/analyzer.py` |
| D2 | Perfiles DPA + comparador | `services/dpa_profiles.py` + `ui/views/my_dpa.py` + `ui/views/compare.py` |
| D3 | Buscador de casos con filtros | `ui/views/search.py` |
| D4 | Detalle de caso + factores Art. 83(2) | `ui/views/case_detail.py` |
| D5 | RAG motor backend (hybrid search + HyDE + intent + RRF + cross-encoder) | `db/rag.py` |
| D6 | Privacy policy scraper (por URL) | `services/profile_scraper.py` |
| D7 | UI design system (warm palette, componentes) | `ui/styles.py` + `ui/components/` |
| D8 | Ingesta base 8,077 docs (Tracker + GDPRhub + EDPB OSS) | `db/ingest.py` |
| D9 | 168K embeddings e5-large-v2 (+138K holdings en progreso) | `db/embed.py` |
| D10 | 765 factores Art. 83(2) extraidos con LLM | `db/extract_factors.py` |
| D11 | Memory service backend | `services/memory.py` |
| D12 | Schema v2 (canonical + medallion) | `db/schema.sql` |
| D13 | Rebrand JurisMind → GDPRScope | `ui/app.py` + `ui/styles.py` |
| D14 | Ingesta noyb: 889 quejas estrategicas | `db/ingest_noyb.py` |
| D15 | Ingesta EDPB OSS: 1,326 decisiones cross-border | `db/ingest_edpb.py` |
| D16 | LangGraph ReAct agent: 9 tools, streaming, CRAG-light | `services/agent.py` |
| D17 | Agent eval pipeline: HR@5 85.1% (47q, 9 categorias) | `eval/eval_agent.py` |
| D18 | Ingesta GDPR law + recitals (99 + 173) | `db/ingest_gdpr_law.py` |

## Eval Progression (confirmado)

```
Single-query RAG:       ~50% HR@5  (confirmado, 163q)
Agente v2 multi-turn:    68% HR@5  (60q, 3 categorias)
Agente v3 + fixes:       85% HR@5  (47q, 9 categorias) ← ACTUAL
  Siguiente: embeddings completos → ~90%+ (7 misses por holdings sin embeber)
```

## Que falta — Tareas para AWS hackathon

### GRUPO 1 — Migrar a Strands Agents SDK

| Tarea | Esfuerzo | Valor |
|---|---|---|
| Swap LangGraph → Strands Agents SDK | 4-6h | OBLIGATORIO (hackathon AWS) |
| Conectar Bedrock (LLM) | 2-3h | OBLIGATORIO |
| Aurora PostgreSQL + pgvector | 4-6h | OBLIGATORIO (reemplaza Docker local) |

### GRUPO 2 — Completar embeddings + eval

| Tarea | Esfuerzo | Valor |
|---|---|---|
| Completar 138K holding embeddings | ~16h compute (overnight) | ALTO — rescata 7 misses |
| Re-eval post-embeddings | 30min | Confirmar mejora |
| Testing + polish UI | 3-4h | ALTO |

### GRUPO 3 — Deploy AWS

| Tarea | Esfuerzo | Valor |
|---|---|---|
| Deploy en AWS (ECS/Lambda + Aurora) | 4-6h | OBLIGATORIO |
| S3 para datos/modelos | 1-2h | MEDIO |
| Video demo | 2-3h | OBLIGATORIO |

## Datos en DB (2026-08-13)

| Tabla | Registros | Fuentes |
|---|---|---|
| documents | **8,077** | Tracker 3,202 + GDPRhub 3,549 + EDPB OSS 1,326 |
| chunks | 309,195 (168K embebidos, 138K holding en progreso) | |
| case_factors | 765 | |
| gdpr_law + recitals | 99 + 173 | |
| noyb_complaints | **889** | 36 paises, 15 statuses |

## Diferenciador competitivo

Nadie combina:
1. Prediccion de multas basada en datos reales (3,841 multas)
2. Perfiles de comportamiento de DPAs
3. Memoria persistente cross-session
4. Busqueda hibrida sobre jurisprudencia GDPR (85% HR@5)
5. Agente multi-turn con CRAG-light (adaptive retrieval)

Todas las calculadoras existentes usan formulas teoricas sobre maximos legales.
GDPRScope usa estadistica sobre decisiones reales.
