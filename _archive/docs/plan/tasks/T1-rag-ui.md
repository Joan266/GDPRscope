# T1 — Intelligent Search con Query Routing + Entity Dossier

**Esfuerzo:** 4-5h | **Valor:** ALTO | **Grupo:** 1 (paralelo) | **Dependencias:** ninguna

## Objetivo

Crear un tab "Search Intelligence" en Streamlit con routing inteligente: detecta el tipo de query y la envia a SQL (entidad, articulo, aggregation) o a RAG (conceptual). El resultado principal son siempre los **documentos de la DB**, no una respuesta sintetizada por LLM.

## Filosofia de producto

Nuestro valor es el DATA, no el LLM:
- El asesor ya tiene su propio LLM (ChatGPT, Claude, Copilot)
- Nosotros proveemos datos estructurados, analytics, dossiers
- El LLM es un commodity — nosotros somos el data layer
- Cero alucinaciones: mostramos datos reales, no texto generado

## Query Routing — cómo funciona

```
Usuario escribe query
        |
        v
  Intent extraction (Haiku ~200ms)
  Detecta: controller_name, jurisdiction, gdpr_articles, sort_by
        |
        v
  ¿Tiene controller_name? ─── SI ──> ENTITY DOSSIER (SQL)
        |                              Todos los casos de esa entidad
        |                              + stats + DPA profile + cross-jurisdiction
        NO
        |
  ¿Tiene gdpr_articles? ──── SI ──> ARTICLE VIEW (SQL)
        |                              Casos con ese articulo
        |                              + stats + DPAs que mas lo sancionan
        NO
        |
  ¿Tiene sort_by/has_fine? ── SI ──> AGGREGATION VIEW (SQL)
        |                              ORDER BY fine_amount DESC
        |                              + stats
        NO
        |
        v
  CONCEPTUAL ──────────────────────> RAG VIEW (vector + BM25)
                                     Busqueda semantica
                                     Devuelve docs con snippets
```

## Que crear

### Archivo: `ui/views/intelligence.py` (~200-250 lineas)

#### Input

```python
query = st.text_input(
    "Search enforcement intelligence",
    placeholder="e.g. Vodafone, Art. 32 healthcare, highest fines 2024...",
)
```

#### Routing

```python
from db.rag import extract_intent, QueryIntent

intent = extract_intent(query)  # Haiku

if intent and intent.controller_name:
    _render_entity_dossier(conn, intent.controller_name, query)
elif intent and intent.gdpr_articles:
    _render_article_view(conn, intent.gdpr_articles, intent.jurisdiction)
elif intent and (intent.sort_by or intent.has_fine):
    _render_aggregation_view(conn, intent)
else:
    _render_rag_results(conn, query)
```

#### Entity Dossier (SQL — ~100 lineas)

Cuando detecta una empresa (ej: "Vodafone", "BBVA", "Google"):

```python
def _render_entity_dossier(conn, controller_name, query):
    cur = conn.cursor()

    # 1. Todos los casos de esta entidad
    cur.execute("""
        SELECT title, fine_amount, decision_date, decision_year,
               jurisdiction, gdpr_articles, authority, outcome
        FROM documents
        WHERE controller_name ILIKE %s
        ORDER BY fine_amount DESC NULLS LAST
    """, (f"%{controller_name}%",))
    cases = cur.fetchall()

    # 2. Stats agregadas
    total_fines = sum(c[1] or 0 for c in cases)
    median_fine = sorted([c[1] for c in cases if c[1]])[ len([...]) // 2 ]
    max_fine = cases[0][1] if cases else 0

    # 3. Articulos mas violados
    cur.execute("""
        SELECT unnest(gdpr_articles) as art, count(*)
        FROM documents WHERE controller_name ILIKE %s
        GROUP BY art ORDER BY count(*) DESC LIMIT 5
    """, (f"%{controller_name}%",))

    # 4. Cross-jurisdiction (misma empresa en otros paises)
    cur.execute("""
        SELECT jurisdiction, count(*), sum(fine_amount)
        FROM documents WHERE controller_name ILIKE %s
        GROUP BY jurisdiction ORDER BY sum(fine_amount) DESC
    """, (f"%{controller_name}%",))

    # 5. Tendencia temporal
    cur.execute("""
        SELECT decision_year, count(*), sum(fine_amount)
        FROM documents WHERE controller_name ILIKE %s
          AND decision_year IS NOT NULL
        GROUP BY decision_year ORDER BY decision_year
    """, (f"%{controller_name}%",))

    # Render todo como dashboard
    st.markdown(f"### {controller_name} — Enforcement Dossier")
    # Stats cards: total cases, total fines, median, max
    # Table: all cases sorted by fine
    # Bar chart: fines by year
    # Articles breakdown
    # Cross-jurisdiction table
```

#### Article View (SQL — ~60 lineas)

Cuando detecta articulos GDPR (ej: "Art. 32 healthcare"):

```python
def _render_article_view(conn, articles, jurisdiction=None):
    # Casos con ese articulo (+ filtro jurisdiccion si existe)
    # Stats: total fines, median, trend
    # DPAs que mas lo sancionan
    # Sectores mas afectados
```

#### Aggregation View (SQL — ~40 lineas)

Cuando detecta sort/aggregation (ej: "highest fines 2024"):

```python
def _render_aggregation_view(conn, intent):
    # SQL: ORDER BY fine_amount DESC / decision_date DESC
    # Filtros de intent (jurisdiction, year_min, year_max)
    # Tabla de resultados
```

#### RAG Fallback (conceptual — ~50 lineas)

Solo para queries conceptuales donde SQL no sirve:

```python
def _render_rag_results(conn, query):
    # Usa el pipeline RAG existente (db/rag.py)
    # embed_query → search_vector + search_text → RRF → fetch_parent
    # Muestra docs con snippets relevantes
    # NO genera respuesta LLM — solo docs
    # Opcionalmente: boton "Export results for your LLM"
```

### Modificar: `ui/app.py`

- Importar `from ui.views import intelligence`
- Anadir tab "Intelligence" (segundo, despues de Analyzer)
- Renderizar `intelligence.render(conn)`

## Entity aliases

Reutilizar `_ENTITY_ALIASES` de `db/rag.py`:
```python
"bbva" → "Banco Bilbao Vizcaya Argentaria"
"vodafone" → "Vodafone España"
"meta" → "Meta Platforms"
```

Y `_resolve_entity_alias()` para expandir nombres cortos.

## Por que esto es mejor que un chatbot RAG

| Aspecto | Chatbot RAG | Intelligence Search |
|---|---|---|
| "Vodafone fines" | 8 chunks aleatorios | 109 casos + stats + cross-jurisdiction |
| Precision | 70% HR@5 | ~100% para entidades/articulos |
| Velocidad | 2-5s (embeddings + LLM) | <500ms (SQL directo) |
| Alucinaciones | Posibles | Cero (datos directos) |
| Conceptual | Funciona | Igual (RAG como fallback) |
| Coste por query | ~$0.01-0.03 | $0 (SQL) / $0.0002 (intent only) |

## Consideraciones

- **Intent extraction**: necesita ANTHROPIC_API_KEY para Haiku. Si no esta, fallback a busqueda SQL simple por texto
- **Entity matching**: usar ILIKE con `%nombre%` — no es perfecto pero cubre el 90% de casos. `_resolve_entity_alias()` ayuda con abreviaturas
- **Modelo e5-large-v2**: solo se carga si la query va a RAG (conceptual). Para SQL routes no se necesita
- **Reutilizar componentes**: `render_precedent_card()` de `ui/components/cards.py` para los casos

## EXTRA: MCP Server (post-core, si hay tiempo)

Exponer los mismos datos como MCP tools para que el asesor conecte su propio LLM:

```python
# tools que el LLM externo puede llamar:
search_cases(controller, jurisdiction, articles, min_fine, max_fine, limit)
get_entity_dossier(entity_name)
get_dpa_profile(jurisdiction)
simulate_fine(articles, sector, jurisdiction, turnover, factors)
get_article_stats(article_number)
```

Esto alinea directamente con el criterio del hackathon "MCP Server" y "Creativity & Originality". El asesor usa SU LLM con NUESTROS datos.

**Esfuerzo MCP:** 3-4h adicionales
**Impacto en jueces:** muy alto (CockroachDB destaca MCP en criterios)

## Criterio de DONE

- [ ] Tab "Intelligence" visible en la UI
- [ ] Query con entidad → entity dossier (lista de casos + stats + cross-jurisdiction)
- [ ] Query con articulo → article view (casos + stats + DPAs)
- [ ] Query con sort/aggregation → tabla ordenada
- [ ] Query conceptual → RAG fallback con docs
- [ ] Intent extraction funciona (se muestra el routing detectado)
- [ ] No crashea con queries vacias o sin resultados
- [ ] Todas las queries SQL 100% parametrizadas
