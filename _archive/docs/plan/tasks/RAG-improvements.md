# RAG Architecture — Estado, Diagnostico y Plan

**Actualizado:** 2026-08-13
**Contexto:** Pipeline RAG en `db/rag.py` + Agente multi-turn en `services/agent.py`. Evaluado con golden_set_v4.json (163 queries doc-first) y golden_set_v3.json (47 queries legacy).

---

## Estado actual — Section-Aware Adaptive RAG

### Implementado en `db/rag.py` (2026-08-13)

1. **Section-Aware Routing**: `classify_query_type()` clasifica la query en 6 tipos (entity/conceptual/scenario/article/fine_sort/cross_juris) y `search_vector_by_sections()` busca solo en las secciones relevantes.
2. **HyDE-Headnote**: Para queries conceptuales/scenario, `hyde_headnote()` genera un headnote hipotetico via LLM y lo embebe como passage.
3. **HyDE-Article**: Para queries article_lookup, `hyde_embed()` genera un excerpt hipotetico de decision.
4. **OpenRouter fallback**: `_call_light_llm()` usa Kimi K2 via OpenRouter como LLM ligero (intent, HyDE, expansion). Anthropic API sin credito.
5. **Headnote embeddings**: 9,214 headnote chunks embebidos (100%). Impacto marginal — no mejoro las metricas significativamente.
6. **Cross-encoder reranker**: `rerank_with_cross_encoder()` con BAAI/bge-reranker-v2-m3. Aplica a **todos** los query types. Top-30 candidatos RRF → re-score por cross-attention.
7. **K ampliado**: K_VECTOR=50, K_TEXT=30 (antes 20/20). Section routing K duplicados.
8. **Soft-filter arms**: Jurisdiction y articles detectados por intent se usan como brazos RRF adicionales (15 hits cada uno), sin reemplazar la busqueda sin filtro.
9. **Golden set v3 cleanup**: Eliminados 13 relevant_source_ids incorrectos de 7 queries.
10. **Golden set v4**: 163 queries generadas con metodologia doc-first (queries generadas DESDE documentos).

### Pipeline actual (verificado contra codigo)

```
Query → Intent Extraction (Kimi K2 via OpenRouter ~500ms)
  │
  ├─ classify_query_type() → entity|article|conceptual|scenario|fine_sort|cross_juris
  │
  ├─ HyDE embed (conceptual/scenario → headnote hipotetico)
  │   HyDE embed (article → excerpt hipotetico)
  │   Direct embed (entity/fine_sort/cross_juris)
  │
  └─ Hybrid Search (N-way RRF):
       Brazo 1: Section-filtered vector search (2-3 secciones, K=20-25 cada una)
       Brazo 2: Unfiltered vector search (K=50)
       Brazo 3: BM25 text search (K=30, sin filtro por seccion)
       Brazo 4: Fine-sorted chunks (solo si sort_by=fine_desc)
       Brazo 5: HyPE question chunks (section='enrichment')
       Brazo 6: Case number direct lookup
       Brazo 7: Soft-filter jurisdiction (15 hits, si intent detecta pais)
       Brazo 8: Soft-filter article (15 hits por articulo, max 2)
       Brazo 9+: Query expansion (2 variantes section-filtered, solo conceptual/scenario)
       │
       → N-way RRF fusion
       → Cross-encoder reranker (top-30 → top_n*2, TODOS los query types)
       → Fetch parent context (dedup por parent, max 2 per doc)
       → Metadata rerank (si sort_by)
       → CRAG Evidence Gate + GDPRhub fallback (si Anthropic disponible)
       → Claude Sonnet response
```

### Section Routing Table (valores actuales en codigo)

```python
SECTION_ROUTING = {
    "entity":      [("facts", 20), ("teaser", 20)],
    "article":     [("headnote", 20), ("holding", 20), ("dispute", 10)],
    "conceptual":  [("headnote", 25), ("dispute", 15), ("holding", 10)],
    "scenario":    [("headnote", 20), ("holding", 20), ("facts", 10)],
    "fine_sort":   [("teaser", 20), ("facts", 10)],
    "cross_juris": [("headnote", 20), ("holding", 20), ("teaser", 10)],
}
```

### Estado de embeddings

| Seccion | Total | Embebidos | Cobertura |
|---|---|---|---|
| teaser | 3,202 | 3,202 | 100% |
| facts | 11,852 | 11,852 | 100% |
| dispute | 684 | 684 | 100% |
| headnote | 9,214 | 9,214 | 100% |
| holding | 282,731 | 146,408 | 52% |

---

## Metricas — progresion historica

### Con golden_set_v3 (47 queries, construido manualmente)

| Version | Fecha | HR@5 | MRR | Cambio principal |
|---|---|---|---|---|
| day8_v3_final | 08-12 | 54.8% | 0.407 | Baseline sin section routing |
| post_embed_regression | 08-13 | 40.4% | 0.315 | +74K holding embeds diluyeron search |
| section_aware_v2 (sin LLM) | 08-13 | 42.6% | 0.296 | Section routing sin headnotes ni LLM |
| section_aware_v3 (+ OpenRouter) | 08-13 | 46.8% | 0.358 | +Kimi K2 intent/HyDE/expansion + headnote embeds |
| section_aware_v4 (+ cleanup) | 08-13 | 48.9% | 0.411 | Eliminados 13 mappings incorrectos del golden set |
| v5 (K=50 + CE all + soft) | 08-13 | 49-53% | 0.38 | K ampliado, CE todos, soft filters. **Variance ±4pp** |

**Nota:** La variance de ±4pp entre runs con el mismo codigo confirmo que 47 queries es insuficiente para medir cambios. Cada query vale ~2pp.

### Con golden_set_v4 (163 queries, doc-first) — RESULTADO DEFINITIVO

| Categoria | N | HR@5 | MRR | Observacion |
|---|---|---|---|---|
| sector | 3 | **100%** | 0.583 | Pocas queries pero funciona |
| cross_jurisdiction | 6 | **67%** | 0.289 | Mejoro vs v3 (0%). Soft-filter jurisdiction ayuda |
| fine_lookup | 28 | **64%** | 0.548 | Fine-sort + intent solido |
| false_premise | 28 | **57%** | 0.562 | El sistema corrige premisas falsas |
| named_entity | 28 | 54% | 0.482 | Queries v4 mas dificiles que v3 |
| edge_case | 6 | 50% | 0.329 | OK |
| conceptual | 28 | 39% | 0.308 | Gap semantico persiste |
| article_lookup | 8 | 38% | 0.312 | Holdings 52% embebidos limita |
| scenario | 28 | **29%** | 0.260 | La peor — hipoteticas no coinciden con ningun doc |
| **TOTAL** | **163** | **49.7%** | **0.420** | |

---

## Que se intento y que resultado dio

### Mejoras que FUNCIONARON

| Mejora | Impacto real | Estado |
|---|---|---|
| Section-aware routing | +2-5pp vs unfiltered | ✅ Implementado |
| Intent extraction (Kimi K2) | Habilita HyDE + soft filters + controller pre-filter | ✅ Implementado |
| HyDE-headnote (conceptual/scenario) | +2-3pp en conceptual | ✅ Implementado |
| Controller pre-filter (entity queries) | named_entity 54-88% | ✅ Implementado |
| Fine-sort injection | fine_lookup 64-100% | ✅ Implementado |
| Cross-encoder reranker (todos) | Mejora ranking dentro de top-30 | ✅ Implementado |
| Soft-filter arms (jurisdiction/article) | cross_jurisdiction 0%→67% | ✅ Implementado |
| K_VECTOR 20→50 | Mas candidatos para CE reranking | ✅ Implementado |
| Golden set v4 doc-first (163 queries) | Metricas fiables y estables | ✅ Generado |

### Mejoras que NO FUNCIONARON o impacto marginal

| Mejora | Resultado | Estado |
|---|---|---|
| Headnote embeddings (9,214 chunks) | Impacto marginal en metricas | ✅ Hecho, no ayudo |
| Aumentar holding embeddings (52%→100%) | No mejora eval si queries vienen de docs ya embebidos | No prioritario |
| Query expansion (2 variantes LLM) | Activo pero impacto no medible (ruido del eval) | ✅ Implementado |
| Golden set v3 cleanup (13 mappings) | +2pp pero el problema era el golden set entero | ✅ Hecho |

### Mejoras DESCARTADAS

| Mejora | Razon de descarte |
|---|---|
| Query translation multilingue | GDPRhub ya esta en ingles — no aplica |
| BM25 section-aware | BM25 es brazo secundario, impacto minimo |
| Headnote generation via LLM | Headnotes embebidos no mejoraron nada |
| Ampliar pool CE de 30 a 50 | Rendimientos decrecientes, 30 es suficiente |

---

## Diagnostico profundo — por que falla

### Posicion del doc relevante en vector search (top-200)

| Posicion | Queries | % | Significado |
|---|---|---|---|
| >200 (no encontrado) | 69% | | Distancia semantica insalvable con embeddings |
| 6-26 (cerca pero fuera de top-5) | 19% | | K=50 + CE parcialmente resuelve |
| 3-5 (en el borde) | 12% | | RRF lo desplaza |

### Conclusion

**El 69% de los fallos son por distancia semantica** — el doc relevante ni siquiera aparece en los 200 candidatos vectoriales mas cercanos. Ningun ajuste de K, reranking, o filtro resuelve esto. Se necesita un cambio de paradigma de busqueda.

---

## Techo confirmado — single-query retrieval

**HR@5 ≈ 50% es el techo real**, confirmado con:
- golden_set_v3 (47 queries): 49-53%
- golden_set_v4 (163 queries doc-first): 49.7%

Consistente con benchmarks de referencia:

| Benchmark | Corpus | Queries | Techo single-query |
|---|---|---|---|
| LegalBench-RAG | 79M chars | 6,858 | ~65% |
| Legal RAG Bench | ~4,000 docs | 400+ | ~60% |
| CLERC | 1,297 casos | 1,297 | ~55% |
| **GDPRScope** | **8,077 docs** | **163** | **50%** |

---

## Agentic Multi-turn — IMPLEMENTADO Y EVALUADO

### Implementacion (`services/agent.py`, 2026-08-13)

Agente LangGraph ReAct con 9 tools, LLM via OpenRouter (Kimi K2).

**Arquitectura:**

```
Usuario → Agente LangGraph (Kimi K2 via OpenRouter)
             │
             ├─ search_precedents(query, jurisdiction, articles)  — semantic search (RAG)
             ├─ search_by_article(article_number, jurisdiction)   — SQL directo
             ├─ search_by_entity(entity_name)                     — SQL directo
             ├─ simulate_fine_tool(articles, jurisdiction, ...)    — EDPB 5-step
             ├─ dpa_profile(country)                              — DPA behavioral profile
             ├─ lookup_law(article_number)                        — GDPR article text
             ├─ analyze_factors(articles, jurisdiction)            — Art. 83(2) factors
             ├─ read_memory(user_id)                              — persistent context
             └─ write_memory(user_id, key, value)                 — save findings
```

**Estrategia de retrieval (system prompt):**

1. Start broad, then narrow — semantic search primero
2. Decompose complex queries — multi-country → separate searches
3. Try different angles on failure — SQL tools cuando embeddings fallan
4. Self-reflect after each search — evaluar si los resultados responden la query
5. Ask the user when stuck — clarificar tras 2-3 intentos fallidos

### Eval v2: Agente vs RAG puro (golden_set_v4, 60 queries, 3 categorias)

**Configuracion v2:**
- Agente slim: solo 3 tools (search_precedents, search_by_article, search_by_entity)
- `search_precedents` usa el pipeline RAG COMPLETO (intent, HyDE, section routing, cross-encoder, soft-filters)
- LLM: Kimi K2 via OpenRouter, max_tokens=1024, temperature=0
- System prompt enfocado: 1-3 tool calls max, solo retrieval

**Resultado definitivo (60 queries, head-to-head):**

| Metrica | RAG puro | Agente v2 | Delta |
|---|---|---|---|
| **HR@5** | 46.7% | **68.3%** | **+21.6pp** |
| **MRR** | 0.394 | **0.464** | **+0.070** |

**Por categoria:**

| Categoria | N | Agent HR@5 | RAG HR@5 | Delta |
|---|---|---|---|---|
| **conceptual** | 24 | **70.8%** | 41.7% | **+29.2pp** |
| **named_entity** | 28 | **75.0%** | 53.6% | **+21.4pp** |
| article_lookup | 8 | 37.5% | 37.5% | +0.0pp |

**Rescue analysis:**
- **15 queries rescatadas** por el agente (RAG fallo, agente acerto)
- **2 queries perdidas** por el agente (RAG acerto, agente fallo)
- **Net gain: +13 queries**

**Queries rescatadas (15):**

| Query | Categoria | Tools | Como lo resolvio |
|---|---|---|---|
| gs4-003 Vodafone Greece SIM swap | named_entity | 3 | Multi-search: entity + semantic, filtro Greece |
| gs4-004 Belgian DPA telecom | named_entity | 1 | search_precedents con full pipeline detecto Belgium |
| gs4-005 COPE Spain cookies | named_entity | 3 | search_by_entity("COPE") + semantic refinement |
| gs4-020 Meta €91M Ireland | named_entity | 1 | search_precedents full pipeline — intent detecta Meta/Ireland |
| gs4-022 Vodafone Italy | named_entity | 1 | search_by_entity("Vodafone") → SQL directo |
| gs4-027 Grindr Norway | named_entity | 1 | search_by_entity("Grindr") → SQL directo |
| gs4-028 Spanish car plate seller | named_entity | 2 | search_by_entity + semantic backup |
| gs4-038 Spanish condominium | conceptual | 2 | Reformulacion + soft-filter Spain |
| gs4-042 Employer CCTV consent | conceptual | 2 | HyDE + section routing a headnote/holding |
| gs4-044 Withdraw consent | conceptual | 1 | Full pipeline: HyDE-headnote + cross-encoder |
| gs4-045 COVID-19 excuse | conceptual | 2 | Multi-angle: semantic + article search |
| gs4-047 Parliamentary committees GDPR | conceptual | 3 | search_precedents + search_by_article + retry |
| gs4-053 Belgian complaint dismissal | conceptual | 2 | search_by_entity + semantic |
| gs4-054 Politician data controller | conceptual | 1 | Full RAG pipeline acerto directamente |
| gs4-058 Journalist liability | conceptual | 3 | Multi-tool: entity + semantic + article |

**Stats del agente v2:**

| Metrica | Valor |
|---|---|
| Tool calls/query avg | 1.7 |
| Messages/query avg | 5.3 |
| Latencia avg | 35.4s |
| Titulos recuperados avg | 10.8 |
| Total eval time | 35.4 min |

### Por que funciona el agente

**Para named_entity (+21.4pp):** `search_by_entity` bypasea embeddings con SQL directo por `controller_name`. El full RAG pipeline con intent extraction detecta jurisdiccion y mejora el filtrado.

**Para conceptual (+29.2pp):** El mayor impacto viene del **full RAG pipeline dentro de `search_precedents`**: intent extraction → HyDE-headnote → section-aware routing → cross-encoder. Queries como "CCTV employer consent" o "politician data controller" se benefician de la generacion de headnotes hipoteticos.

**Para article_lookup (+0pp):** Sin mejora. El agente llama `search_by_article` pero esta tool ya existia como brazo del pipeline RAG. El cuello de botella es que holdings solo estan 52% embebidos.

### Por que aun falla (19 misses)

Los 19 misses del agente se dividen en:

1. **Distancia semantica insalvable (11):** El doc relevante no aparece ni por embeddings ni por SQL. Ninguna cantidad de reformulacion o multi-tool lo resuelve.
2. **Entity naming mismatch (4):** El nombre en la query no coincide con `controller_name` en DB. Ej: "Trive Credit Spain" vs nombre real del controlador.
3. **Article lookup con holdings sin embeddings (3):** Queries de articulo donde el holding relevante no tiene embedding.
4. **Fuzzy match failure (1):** El agente encontro el doc pero el titulo extractor no lo reconocio.

---

## Plan siguiente — GraphRAG

### Resumen de techos

```
Single-query RAG (CONFIRMADO):         ~50% HR@5  (163 queries)
Agentic multi-turn (CONFIRMADO, 60q):  ~68% HR@5  (+18pp, 3 categorias)
  + GraphRAG knowledge graph:          ~80% HR@5  (estimado)
```

### GraphRAG — Knowledge Graph (estimado: +10-15pp sobre agente)

**Problema que resuelve:** El agente ya cubre queries con entidades y articulos via SQL. GraphRAG resolveria queries **conceptuales y de scenario** donde ni embeddings ni SQL funcionan — navegando relaciones entre casos.

**Como funciona:**

```
Query: "Art. 32 fines in healthcare"

Agente actual:
  search_by_article("32") → encuentra casos con Art. 32, pero sin filtro de sector
  search_precedents("security breach hospital") → embeddings, falla si texto dice
  "medidas de seguridad inadecuadas en centro sanitario"

Con GraphRAG:
  graph_traverse(article="32", sector="healthcare") → navega relaciones:
    Art. 32 → CITA → Caso OLVG, Caso HagaZiekenhuis, ...
                      → filtrar sector=healthcare
                      → devolver top-5 por fine_amount DESC
  No necesita coincidencia semantica. Navega relaciones.
```

**3 subgrafos:**

```
Statutory Graph:          Art. 32 ←cita→ Caso X, Caso Y, Caso Z
Precedent Graph:          Caso A ←sigue→ Caso B ←distingue→ Caso C
Case Relationship Graph:  Caso X ~similar_sector~ Caso Y
```

**Ya tenemos los datos:**
- `gdpr_articles[]` en cada documento (7,800 docs con articulos)
- `jurisdiction`, `authority`, `fine_amount` (metadata estructurada)
- Tabla `citations` (vacia, preparada)

**Lo que falta:**
1. Construir el grafo (nodos: articulos, casos, DPAs, sectores; aristas: cita, emitido_por, sector)
2. Tool `graph_traverse` para el agente
3. Sector classification de los docs existentes (NLP o LLM)

**Referencia:** LegalGraphRAG (2026) — Multi-Agent Graph RAG con 3 subgrafos legales jerarquicos.

**Conclusion:** El agente multi-turn ya rompio el techo de 50% (68.3% confirmado en 60q). Rescato 15 queries, perdio solo 2. GraphRAG es el siguiente paso para los 17 misses persistentes (ambos fallan).
