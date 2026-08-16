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

## Pivot validado: GDPR Fine Defense Intelligence (08-16)

**Score: 7.15/10 — GO unanime (4/4 agentes)**

### Nuevo target: Law firms especializadas en privacy/data protection

El mismo producto, diferente buyer y messaging:
- ANTES (DPO): "Busca jurisprudencia" → 3-5 consultas/mes, dolor 5/10
- AHORA (Law firm): "Defiende contra multas GDPR" → pull demand, WTP validada

### Por que funciona

- 40% de EUR 7.1B en multas anuladas o bajo challenge
- Reducciones probadas: Amazon -100%, notebooksbilliger -93%, 1&1 -90%, BA -89%
- Lawyers pagan $107-639/mo por Westlaw/Lexis que NO es GDPR-specific
- Zero competidores VC-backed en el nicho (Aptus AI es lo mas cercano, pero compliance no defense)
- Expansion natural: AI Act (ago 2026) + NIS2 + DORA = mismas firms, 3-5x TAM

### 4 features clave

1. **Similar Case Finder** — busqueda semantica cross-jurisdiccional por articulo/sector/infraccion
2. **Fine Range Predictor** — dado caso con caracteristicas X, que rango de multas han puesto otros DPAs
3. **Inconsistency Detector** — detecta precedentes donde misma infraccion tuvo multas muy diferentes
4. **Appeal Success Analyzer** — casos donde multas fueron anuladas/reducidas y por que

### Pricing

| Tier | Precio | Features |
|------|--------|----------|
| Analyst | EUR 99/mo | Busqueda + precedentes basicos |
| Counsel | EUR 249/mo | Analisis cross-jurisdiccional + fine calculator |
| Defense | EUR 499/mo | Defense briefs + DPA profiling + multi-regulation |
| Add-on | EUR 150-500/caso | Deep defense brief one-off |

### Posicionamiento

Complemento a Westlaw, no reemplazo:
- Westlaw: case law tradicional UK/US, sentencias tribunales
- GDPRScope: decisiones administrativas DPAs, cross-jurisdiccional, fine analytics
- El abogado paga EUR 400/mo Westlaw + EUR 99-249/mo GDPRScope

### Target users (3 perfiles)

1. Boutiques privacy (activeMind, Knyrim-Trieb): 100% data protection, 5-30 abogados
2. Equipos privacy en BigLaw (Bird&Bird 140+, Hogan Lovells, DLA Piper): 10-40 abogados/equipo
3. In-house counsel multinacionales: evaluacion de riesgo proactiva

Total: ~5,000-13,000 usuarios potenciales. A EUR 249/mo × 500 pagando = EUR 1.5M ARR

### Riesgos a mitigar

1. Hallucination: citation grounding (ya implementado) lo resuelve — cada output anclado a chunk real
2. SOC 2: obligatorio para enterprise ($10-40K/yr, 2-4 meses) — no bloqueante para MVP/boutiques
3. EU AI Act: posicionar como "search/analytics tool" no "AI legal advisor"
4. Sales cycle: 4-9 meses mid-market

### Validacion tecnica: backtesting (ANTES de contactar abogados)

**Test 1 — Precedent retrieval con casos reales de apelacion**
Simular queries de abogado defensor con 6 casos donde multas fueron reducidas/anuladas:
- Amazon EUR 746M (anulada) — buscar precedentes Art. 5(1)(a)
- VW EUR 4.3M (anulada) — buscar precedentes negligencia vs intencion
- notebooksbilliger EUR 10.4M → EUR 700K (-93%) — videovigilancia empleados
- 1&1 Telecom EUR 9.55M → EUR 900K (-90%) — verificacion identidad
- BA GBP 183M → GBP 20M (-89%) — breach + cooperacion
- Marriott GBP 99M → GBP 18.4M (-81%) — breach heredado M&A
Verificar: el agente encuentra los precedentes que los abogados reales usaron?

**Test 2 — Fine range prediction (hold-out)**
Ocultar 200 casos random de la DB. Predecir rango de multa con los restantes.
Target: 70%+ accuracy en rango (no numero exacto, sino orden de magnitud).

**Test 3 — Inconsistency detection**
Pedir al sistema inconsistencias cross-jurisdiccionales conocidas:
- Art. 5(1)(a): Irlanda EUR 405M vs Belgica EUR 50K (8,000x diferencia)
- Art. 32: multas de EUR 90 a EUR 35M por mismo articulo
- Videovigilancia: Alemania EUR 10.4M vs Espana EUR 6K

### Siguiente paso

1. Ejecutar los 3 backtests con infraestructura actual (no requiere build nuevo)
2. Si resultados positivos → contactar 5 abogados privacy para validar dolor
3. Si confirman 20+ horas research/caso → construir MVP React
