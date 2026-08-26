# Plan: Agentic RAG — 3 mejoras

Fecha: 2026-08-03
Objetivo: subir HR@5 de 48% a ~70%+ resolviendo los fallos reales identificados en la eval.

---

## Contexto: por qué falla el RAG actual

El retrieval híbrido (vector + BM25) funciona bien para queries semánticamente similares
al contenido de los chunks. Falla en tres patrones concretos:

1. **Superlativo / ordenación**: "highest fine", "most recent" → cosine similarity no entiende rangos
2. **Entidad exacta con corpus grande**: "Vodafone España" → más docs = más ruido, la entidad pierde posición
3. **Síntesis multi-doc**: "typical fine ranges" → no hay un solo doc correcto, el LLM tiene que sintetizar

---

## Mejora 1 — Índice del corpus (corpus summary)

### Qué es
Un bloque JSON pequeño (~200 tokens) que describe qué hay en la base de datos.
Se inyecta en el system prompt del LLM antes de cada consulta.

### Contenido del índice

```json
{
  "total_docs": 993,
  "date_range": "2018–2026",
  "authorities": {
    "AEPD (Spain)": 862,
    "APD/GBA (Belgium)": 89,
    "ANSPDCP (Romania)": 31
  },
  "top_gdpr_articles": ["Art. 5", "Art. 6", "Art. 13", "Art. 32", "Art. 35"],
  "fine_range_eur": { "min": 500, "max": 14400000 },
  "sectors": ["banking", "telecom", "tech/AI", "transport", "retail", "public sector"]
}
```

### Cómo se genera
Script `db/build_corpus_index.py` que corre una query SQL de stats y escribe
`data/corpus_index.json`. Se regenera al ingestar nuevos docs.

```sql
SELECT
  COUNT(*)                                 AS total_docs,
  MIN(decision_year), MAX(decision_year)   AS year_range,
  authority_abbrev, COUNT(*)               AS by_authority,
  MIN(fine_amount), MAX(fine_amount)       AS fine_range
FROM documents GROUP BY authority_abbrev;
```

### Dónde se inyecta
En `rag.py → build_prompt()`, al principio del system prompt:

```
You are JurisMind. The database contains:
- 993 GDPR decisions (2018–2026)
- Authorities: AEPD Spain (862), APD/GBA Belgium (89), ...
- Fines: €500 – €14,400,000
- Key articles: Art. 5, 6, 13, 32, 35
Use this to calibrate what you can and cannot find.
```

### Impacto esperado
El LLM ya no alucina casos que no existen. Sabe si una jurisdicción está cubierta
antes de intentar buscarla.

---

## Mejora 2 — Pre-filtrado via extracción de intención

### Qué es
Antes de lanzar el vector search, un LLM pequeño (o el mismo Claude con un prompt corto)
analiza la pregunta del usuario y extrae parámetros estructurados.
Esos parámetros se convierten en filtros SQL parametrizados — **nunca SQL libre**.

### Esquema de parámetros extraídos

```python
@dataclass
class QueryIntent:
    controller_name: str | None   # "Vodafone", "Amadeus", "BBVA"
    authority:       str | None   # "AEPD", "CNIL", "ICO"
    jurisdiction:    str | None   # "Spain", "France"
    gdpr_articles:   list[str]    # ["Article 32", "Article 6"]
    year_min:        int | None   # 2020
    year_max:        int | None   # 2026
    sort_by:         str | None   # "fine_desc" | "date_desc" | None
    has_fine:        bool | None  # True si pregunta por multas
```

### Prompt de extracción (lllamada a Claude, max_tokens=100)

```
Extract structured search parameters from this legal query.
Return ONLY valid JSON matching this schema: {...}
If a field is not mentioned, return null.
Query: "{user_question}"
```

### Cómo se usan los parámetros

Los parámetros se mapean a filtros SQL **parametrizados** en funciones existentes:

```python
# Filtro por entidad concreta → busca en controller_name antes del vector search
if intent.controller_name:
    cur.execute(
        "SELECT id FROM documents WHERE controller_name ILIKE %s",
        [f"%{intent.controller_name}%"]
    )
    # Si encuentra docs → los boost en el RRF

# Filtro por artículo → ya existe en _build_filter_clause()
if intent.gdpr_articles:
    filters["gdpr_article"] = intent.gdpr_articles[0]

# Ordenación por fine → query SQL fija, no libre
if intent.sort_by == "fine_desc":
    # Post-procesa resultados RRF ordenando por fine_amount del doc padre
    contexts.sort(key=lambda x: x.get("fine_amount") or 0, reverse=True)
```

**Nunca se pasa SQL generado por el LLM a la base de datos.**
El LLM solo rellena valores en queries predefinidas y parametrizadas.

### Casos que resuelve

| Query | Intent extraído | Fix |
|---|---|---|
| "highest GDPR fine" | sort_by=fine_desc | Re-rank por fine_amount |
| "Vodafone España fine" | controller_name=Vodafone | Pre-filter por controller |
| "most recent 2026 decisions" | year_min=2026, sort_by=date_desc | Filter + sort |
| "Article 32 violations" | gdpr_articles=[Art.32] | Filter por artículo |
| "DPIA cases" | gdpr_articles=[Art.35] | Filter por artículo |

---

## Mejora 3 — Búsqueda iterativa (max 2 iteraciones)

### Qué es
Después de la primera búsqueda, el LLM evalúa si los resultados son suficientes.
Si no, genera una query de refinamiento y busca una segunda vez.
Límite estricto: **2 iteraciones máximo** para controlar latencia y coste.

### Flujo

```
[Iteración 1]
  user_query → intent extraction → pre-filtered hybrid search → contexts_1

[Evaluación LLM]
  ¿Los contexts_1 responden la pregunta?
  → SI: generar respuesta final
  → NO: ¿qué falta? → generar refined_query

[Iteración 2]
  refined_query → hybrid search (sin pre-filter, búsqueda más amplia) → contexts_2
  contexts = deduplicate(contexts_1 + contexts_2)[:top_n]
  → generar respuesta final
```

### Prompt de evaluación (max_tokens=50)

```
Context retrieved: {titles_list}
Question: {question}
Are these results sufficient to answer? Reply JSON: {"sufficient": true/false, "missing": "..."}
```

### Criterio de parada

- `sufficient: true` → para
- `sufficient: false` + iteración 2 completada → para igualmente (no hay iteración 3)
- Si no hay resultados en iteración 2 → responde "no encontré casos suficientes"

### Coste adicional
~2 llamadas LLM extra por query en el peor caso:
- Evaluación: ~50 tokens → ~$0.0003
- Refinement search: ~100 tokens → ~$0.0006
Total: <$0.001 por query adicional. Asumible.

### Latencia adicional
- Evaluación + segunda búsqueda: +3-5 segundos en el peor caso
- Mostrar spinner con estado: "Buscando... Refinando resultados..."

---

## Implementación — orden y archivos

### Día 1: Mejora 1 + Mejora 2

```
db/build_corpus_index.py   (nuevo — genera data/corpus_index.json)
db/rag.py                  (modificar — añadir intent extraction + pre-filtering)
  ├── extract_intent(query_text) → QueryIntent
  ├── apply_intent_filters(intent, filters) → dict
  ├── rerank_by_metadata(contexts, intent) → list
  └── build_prompt() → inyecta corpus_index en system prompt
ui/catalog.py              (sin cambios — la UI ya funciona)
```

### Día 2: Mejora 3 + eval completa

```
db/rag.py                  (añadir iterative search loop)
  └── query() → añade evaluate_sufficiency() + second pass si needed
eval/run_eval.py           (sin cambios)
eval/golden_set.json       (opcional: añadir preguntas multi-jurisdicción)
```

---

## Métricas objetivo

| Métrica | Actual (v2) | Objetivo post-mejoras |
|---|---|---|
| HR@5 global | 40% | ~65-70% |
| named_entity HR@5 | 75% | 95%+ |
| fine_lookup HR@5 | 75% | 90%+ |
| article_lookup HR@5 | 20% | 50%+ |
| Latencia media | ~10s | <15s (con iteración) |

---

## Lo que NO haremos

- **No SQL libre**: el LLM nunca genera SQL. Solo rellena valores en queries predefinidas.
- **No más de 2 iteraciones**: latencia controlada, coste predecible.
- **No cambiar el schema**: todas las mejoras van en la capa de aplicación.
- **No cambiar embed.py**: los embeddings actuales son correctos.
