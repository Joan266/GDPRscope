# Hallazgo: Source Depth Gap — Tracker vs GDPRhub

**Fecha:** 2026-08-06
**Estado:** Diagnosticado, pendiente de fix

---

## Problema

El Enforcement Tracker (3.202 docs) y GDPRhub (1.359 docs) compiten en el mismo ranking de retrieval, pero tienen profundidades radicalmente distintas:

| Fuente | Parent chunk avg | Contenido |
|---|---|---|
| Tracker | **328 chars** | Ficha: titulo, autoridad, multa, articulos, sector, tipo de violacion |
| GDPRhub | **1.066 chars** (hasta 131K) | Hechos detallados, holding legal, razonamiento del DPA |

Cuando el LLM recibe una ficha del Tracker de 6 campos, rellena los huecos con su conocimiento parametrico y produce una respuesta que aparenta ser un analisis profundo pero no esta soportada por el contexto recuperado.

## Almacenamiento actual

**Una sola tabla `documents`**, no dos. Tracker y GDPRhub conviven diferenciados por `source`:

```
documents
├── source = 'enforcement_tracker'  →  source_id = "58" (numerico secuencial)
├── source = 'gdprhub'             →  source_id = "ICO - British Airways" (titulo pagina)
└── source = 'eurlex'              →  source_id = CELEX (pendiente)
```

**No hay ID compartido ni tabla de cross-reference.** La unica forma de enlazar un doc del Tracker con su version GDPRhub es fuzzy match por `controller_name` + `jurisdiction`.

Campos clave por fuente:

| Campo | Tracker | GDPRhub |
|---|---|---|
| source_id | numerico ("58") | titulo ("ICO - British Airways") |
| case_number | **0 de 3.202** | 1.353 de 1.359 |
| ecli | 0 | 24 |
| summary_facts | NULL | texto completo de hechos |
| summary_holding | NULL | texto completo del holding |
| full_text | NULL | NULL (no se ingesta) |

## Datos concretos

Casos del adversarial eval:

| Caso | Tracker | GDPRhub en DB | GDPRhub en API | Resultado eval |
|---|---|---|---|---|
| Clearview AI (Garante) | 401 chars | 159.185 chars | - | hallucination |
| WhatsApp (DPC Ireland) | 664 chars | 2.919 chars | - | hallucination |
| Spotify (IMY Sweden) | 297 chars | 257.190 + 159.404 chars | - | hallucination |
| British Airways (ICO) | 332 chars | **no ingestado** | `ICO - British Airways` | hallucination |
| H&M (Hamburg DPA) | 301 chars | **no ingestado** | `HmbBfDI (Hamburg) - H&M` | hallucination |

- British Airways y H&M **existen en GDPRhub** pero no se han ingestado (solo 1.359 de 6.491+ disponibles)
- Clearview, WhatsApp y Spotify **ya tienen version completa en la DB** pero el retrieval devuelve la ficha del Tracker
- Solapamiento total: 273 casos existen en ambas fuentes (mismo controller + jurisdiction)

## Impacto medido

Adversarial eval `out_of_jurisdiction`: **0/6 correcto** (100% alucinaciones).
Todas causadas por este gap — el LLM presenta metadata del Tracker como analisis fundamentado.

## Causa raiz

1. Tracker y GDPRhub compiten en el mismo vector space sin distincion
2. Las fichas del Tracker ganan por keyword match exacto (contienen controller name en el titulo)
3. `build_prompt()` no distingue la profundidad de la fuente
4. El Evidence Gate ve fine_amount + articles en la ficha y puntua alto (~0.8) cuando deberia puntuar bajo
5. No hay mecanismo de "enriquecimiento": si el Tracker retrieva un caso que existe en GDPRhub, no se busca la version completa
6. Solo se ha ingestado el 21% de GDPRhub (1.359/6.491) — muchos casos del Tracker tienen version completa disponible pero no ingestada

## Evaluacion del schema vs literatura

### Lo que esta BIEN (alineado con best practices)

1. **Parent-child chunking** — Patron lider para documentos legales (FutureAGI 2026, LanceDB). Child pequeno para retrieval, parent grande para contexto.
2. **Hybrid search 4-way RRF** — Recomendado por "Towards Reliable Retrieval in RAG for Large Legal Datasets" (arxiv 2510.06999, 2025). Nuestra implementacion es mas sofisticada que lo estandar.
3. **Tabla `citations`** — Preparada para GraphRAG. Confirmado como futuro del RAG legal por LegalGraphRAG (arxiv 2605.28120, 2025).
4. **`user_memory` con embeddings** — Diferenciador real. La mayoria de RAGs legales no tienen memoria cross-session.
5. **`research_sessions`** — Feedback loop y trazabilidad. Recomendado por Databricks RAG Pipeline Guide.
6. **PostgreSQL/CockroachDB como vector store** — Correcto para <5M vectores (Encore 2026, pgvector Production Guide). Nuestros ~12K chunks entran sobrados.

### Lo que FALTA (gaps respecto a la literatura)

1. **Sin cross-reference entre fuentes** — MES-RAG (arxiv 2503.13563) describe exactamente nuestro problema: "information from different entities is intermingled, leading to retrieval noise". Proponen entity-centric data representation.
2. **Sin indicador de calidad/completitud** — Microsoft Azure RAG Enrichment Guide recomienda confidence score y source grounding por campo. RA-RAG (arxiv 2410.22954) estima fiabilidad por fuente. Nosotros tratamos 332 chars igual que 131K chars.
3. **Sin deduplicacion cross-source** — La deduplicacion debe seleccionar la "best version" (most authoritative source). Nuestro UNIQUE(source, source_id) solo previene duplicados dentro de una fuente.
4. **Comentarios del schema desactualizados** — Header dice "Amazon Titan V2" pero usamos e5-large-v2.

### Lo que NO hay que cambiar

La estructura de tablas es correcta. No necesitamos rediseno — necesitamos campos nuevos y una estrategia de enlace.

## Cambios en schema propuestos

```sql
-- 1. Indicador de completitud
ALTER TABLE documents ADD COLUMN content_depth TEXT DEFAULT 'full';
-- 'full'    = decision completa con hechos + holding (GDPRhub)
-- 'summary' = solo metadata (Enforcement Tracker)
-- 'partial' = algunos campos pero no texto completo

-- 2. Enlace canonico entre fuentes
ALTER TABLE documents ADD COLUMN canonical_id UUID REFERENCES documents(id);
-- Si un doc del Tracker tiene version GDPRhub, canonical_id apunta al GDPRhub
-- NULL = es el registro canonico o no tiene version enriquecida

-- 3. Actualizar comentario del embedding model
COMMENT ON COLUMN chunks.embedding IS 'intfloat/e5-large-v2, 1024 dims, local';
```

## Plan de implementacion

### Fase 1 — content_depth + source tagging (rapido, ~1h)
1. ALTER TABLE: anadir `content_depth`
2. UPDATE: marcar todos los Tracker como `summary`, GDPRhub como `full`
3. En `build_prompt()`: etiquetar contextos `summary` como `[ENFORCEMENT TRACKER SUMMARY]`
4. Regla en system prompt: "Para SUMMARY, presenta solo datos visibles y advierte que no hay texto completo"
5. Evidence Gate: si avg content < 400 chars, bajar score

### Fase 2 — canonical_id + source enrichment (medio dia)
1. ALTER TABLE: anadir `canonical_id`
2. Script de match: enlazar Tracker docs con GDPRhub por controller_name + jurisdiction
3. En retrieval post-processing: si hit es Tracker con canonical_id, swap por version GDPRhub

### Fase 3 — Ingestar todo GDPRhub (horas de ejecucion)
1. Relanzar ingest.py con --gdprhub-limit 6500
2. Re-embeber nuevos chunks
3. Rebuild corpus_index.json
4. Re-evaluar ambos evals

### Recomendacion
Empezar por Fase 1 (impacto inmediato), luego Fase 3 (solucion de fondo), Fase 2 si quedan gaps.

## Metricas objetivo

- Adversarial `out_of_jurisdiction`: de 0/6 a 4+/6
- Standard eval: mantener HR@5 > 75%, Faithfulness > 70%

## Referencias

- [Advanced RAG Chunking 2026](https://futureagi.com/blog/advanced-chunking-techniques-for-rag/)
- [Reliable Retrieval for Legal Datasets](https://arxiv.org/pdf/2510.06999)
- [LegalGraphRAG](https://arxiv.org/pdf/2605.28120)
- [Microsoft Azure RAG Enrichment](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-enrichment-phase)
- [RA-RAG: Source Reliability](https://arxiv.org/pdf/2410.22954)
- [MES-RAG: Entity-Storage](https://arxiv.org/pdf/2503.13563)
- [RAG Deduplication at Scale](https://sabarishkumarg.medium.com/designing-rag-architectures-that-scale-chunking-deduplication-and-accuracy-improvements-1adb76dbd8ec)
- [pgvector Production RAG 2026](https://devstarsj.github.io/2026/04/04/postgresql-pgvector-pgvectorscale-rag-production-guide-2026/)
