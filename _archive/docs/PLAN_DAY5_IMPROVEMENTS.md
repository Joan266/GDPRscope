# JurisMind — Plan de Mejoras Day 5
## Deadline hackathon: 18 agosto 2026 (12 días)

---

## Estado de partida (2026-08-06)

| Métrica | Valor actual | Target demo |
|---|---|---|
| HR@5 | 84.2% | >90% |
| MRR | 0.851 | >0.90 |
| Faithfulness | 62.9% | >80% |
| Misses retrieval | 3/19 | ≤1/19 |
| Self-improving (auto-ingest) | No | Sí |
| Preguntas globales/temáticas | No funciona | Funciona |

Commit base: `f656332` — eval day4 final, todos los targets del hackathon cumplidos.

---

## Por qué faithfulness no es 100% — diagnóstico de causas

Antes de implementar, es importante entender que hay **tres causas distintas** con soluciones distintas.

### Causa A — El doc no está en DB (o sin embedding)
El LLM recupera docs irrelevantes → no tiene contexto real → interpola desde training.
Ejemplos: gs-015 (EXP202406208, API breach sin embedding completo), gs-008 (EXP202213437).
**Solución:** CRAG Evidence Gate + GDPRhub fallback (PRIO 2).

### Causa B — El doc está en DB pero el detalle no está en el texto del chunk
`fine_amount`, `gdpr_articles` son metadatos en la DB y en el corpus_index del system prompt.
El LLM los ve y los cita correctamente. El juez de faithfulness solo ve los chunks (texto) y
los marca como "no grounded" aunque sean correctos.
Ejemplo: gs-001 (Amadeus, €14.4M citado desde corpus_index, no desde chunk text).
**Solución:** inyectar metadatos en el bloque de contexto en retrieval time (PRIO 1).

### Causa C — El LLM interpola paramétrico aunque tenga contexto
Tiene los docs correctos pero añade detalles del training. Las reglas 7+8 del system prompt
atacan esto. El límite real: Claude conoce casos GDPR reales de su training y es difícil
inhibirlo completamente sin degradar la calidad de respuesta.
**Solución parcial:** las reglas ya implementadas + Evidence Gate que fuerza abstención.

---

## Las 5 mejoras planificadas

---

### PRIO 1 — Metadatos en bloque de contexto [~1h]

**Qué:** al construir el contexto para el LLM y para el juez, anteponer una línea de
metadatos clave al texto de cada chunk. Sin re-embeber nada — solo cambia cómo se
presenta el contexto en `fetch_parent_context()`.

**Por qué en retrieval time y no en ingest:**
- Sin coste (no re-embebe)
- Inmediato — no requiere pipeline
- Los casos con fine_amount + articles ya se encuentran bien por BM25 + case-number
  pinning; mejorar el embedding es marginal para estos docs

**Dónde:** `db/rag.py` — función `fetch_parent_context()` o donde se construye
el bloque de texto por citación.

**Antes:**
```python
# Cada bloque de contexto:
"[Case Title] | [Authority] | [Year]\n[chunk text...]"
```

**Después:**
```python
# Cada bloque de contexto:
"[Case Title] | [Authority] | [Year]
Fine: EUR 14,400,000 | Articles: Art. 6 GDPR, Art. 14 GDPR
[chunk text...]"
```

**Impacto estimado:** Faithfulness 62.9% → ~70% (elimina falsos positivos del juez).

**Verificación:**
```bash
export $(grep -v '^#' .env | xargs)
PYTHONUTF8=1 python eval/run_eval.py --golden eval/golden_set.json --llm \
  --out eval/results_day5_prio1.json
# Comparar faithfulness con results_day4_llm_final.json
```

---

### PRIO 2 — CRAG Evidence Gate + GDPRhub fallback + auto-ingest [~4h]

Esta es la **killer feature** del hackathon: un sistema que se auto-mejora con el uso.

#### Arquitectura completa

```
Query del abogado
        |
        v
1. RETRIEVE — hybrid 4-way RRF (existente)
        |
        v
2. EVIDENCE GATE — LLM evalúa calidad del contexto recuperado
   Prompt corto: "Do these documents contain specific, citable information
   to answer the query? Score 0.0-1.0 and explain why."
        |
        +--------+--------+
        |        |        |
      >=0.65  0.35-0.65  <0.35
      CORRECT AMBIGUOUS INCORRECT
        |        |        |
        |    Combinar  Descartar
        |    DB + ext  todo DB
        |        |        |
        |        v        v
        |   EXTERNAL SEARCH (PRIO 2b)
        |   search_gdprhub_external(query, intent)
        |        |
        |        v
        |   AUTO-INGEST (PRIO 2c)
        |   Si doc nuevo encontrado:
        |   ingest_on_demand() -> embed() -> retrieve de nuevo
        |        |
        +--------+
        |
        v
3. GENERATE — solo desde contexto verificado
   Si ninguna fuente tiene info suficiente:
   "No encuentro información específica sobre este caso en nuestra base
   de datos ni en GDPRhub. Los casos más cercanos son: [lista]"
```

#### 2a — Evidence Gate

**Dónde:** nuevo método `_evaluate_evidence_quality()` en `db/rag.py`, llamado
antes de `build_prompt()` / `call_llm()`.

```python
def _evaluate_evidence_quality(
    ac: anthropic.Anthropic,
    query_text: str,
    citations: list[dict],
) -> float:
    """
    Evalúa si los chunks recuperados tienen suficiente evidencia específica
    para responder la query sin recurrir a conocimiento paramétrico.
    Retorna score 0.0-1.0.
    """
    if not citations:
        return 0.0

    context_summary = "\n".join(
        f"- {c.get('title','')} | Fine: {c.get('fine_amount')} | "
        f"Articles: {c.get('gdpr_articles')} | Snippet: {(c.get('content','')[:200])}"
        for c in citations[:5]
    )

    prompt = f"""Query: {query_text}

Retrieved documents:
{context_summary}

Does this context contain specific, directly citable information (case numbers,
fine amounts, article violations, factual findings) to answer the query?

Respond with JSON only: {{"score": 0.0-1.0, "reason": "brief explanation"}}
Score guide: 1.0=complete answer in context, 0.5=partial, 0.0=no relevant info"""

    msg = ac.messages.create(
        model=MODEL_ID_LLM,
        max_tokens=100,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    import json as _json
    try:
        data = _json.loads(msg.content[0].text.strip())
        return float(data.get("score", 0.5))
    except Exception:
        return 0.5  # default ambiguous
```

Thresholds: CORRECT >= 0.65 | AMBIGUOUS 0.35-0.65 | INCORRECT < 0.35.

#### 2b — GDPRhub API fallback

**Dónde:** nuevo método `_search_gdprhub_external()` en `db/rag.py`.
Reutiliza la lógica de `db/ingest.py` — la API ya está integrada.

```python
def _search_gdprhub_external(
    query_text: str,
    intent: QueryIntent,
    limit: int = 5,
) -> list[dict]:
    """
    Busca en GDPRhub MediaWiki API casos relacionados con la query.
    Retorna lista de dicts con title, url, snippet.
    """
    # Reescribir query para búsqueda óptima en GDPRhub
    # Si intent tiene controller -> buscar por controller
    # Si intent tiene gdpr_articles -> buscar por artículo
    # Si intent tiene sort_by=fine -> filtrar por fine_amount
    ...
```

#### 2c — Auto-ingest on-demand

**Dónde:** nueva función `ingest_document_on_demand(title: str) -> str | None`
que llama al pipeline existente para un solo documento.

**Importante:** idempotente — si el doc ya está en DB, retorna el ID existente sin duplicar.

#### Modificación en `query()` (flujo principal)

```python
# Tras retrieve, antes de build_prompt:
evidence_score = _evaluate_evidence_quality(ac, query_text, citations)
log.info("Evidence Gate score: %.2f", evidence_score)

if evidence_score < 0.65:
    # Fallback externo
    external_docs = _search_gdprhub_external(query_text, intent)
    if external_docs:
        # Auto-ingest on-demand
        for doc in external_docs:
            ingest_document_on_demand(doc["title"])
        # Re-retrieve incluyendo nuevos docs
        citations = _re_retrieve(cur, query_text, intent, top_n)

if evidence_score < 0.35 and not citations:
    # Abstención estructurada
    return QueryResult(
        answer="No encuentro información suficiente en la base de datos "
               "ni en GDPRhub para responder esta pregunta con precisión...",
        citations=[], ...
    )
```

#### Notificación en UI (streaming)

En `ui/catalog.py`, los estados se muestran en tiempo real:
```
🔍 Buscando en base de datos local...
⚠️  Contexto insuficiente (score: 0.28). Consultando GDPRhub...
📥  Caso encontrado: PS/00037/2020. Añadiendo a la base de datos...
✅  Generando respuesta desde fuentes verificadas...
```

**Impacto estimado:** Faithfulness 70% → ~82%. HR@5 84.2% → ~90%+.
Los 3 misses actuales (gs-008, gs-014, gs-015) se resuelven si están en GDPRhub.

**Verificación:**
```bash
# Test manual: pregunta que no está en DB
PYTHONUTF8=1 python db/rag.py \
  --query "What did AEPD decide in case EXP202213437 about bank data breach?" \
  --user-id test
# Debe: detectar baja evidencia → buscar en GDPRhub → ingestar → responder

# Verificar que el doc quedó en DB:
PYTHONUTF8=1 python db/dbinspect.py show "AEPD (Spain) - EXP202213437"
```

---

### PRIO 3 — Llenar tabla `citations` [~2h]

**Qué:** parsear el texto de todos los documentos ya en DB buscando referencias
a otros números de expediente. Insertar en la tabla `citations` (ya existe en schema,
está vacía). Coste: 0 llamadas LLM, solo regex + SQL.

**Por qué:** habilita multi-hop queries y mejora los casos `edge_case` del golden set
donde una decisión referencia explícitamente otra.

**Nuevo script:** `db/build_citations.py`

```python
"""
Extrae referencias cruzadas entre documentos GDPR usando regex sobre
el texto de los chunks, e inserta en la tabla citations.

Idempotente: ON CONFLICT DO NOTHING.
"""

CASE_REF_PATTERN = re.compile(
    r'\b(?:PS|PD|EXP|E|TD|AN)[-/]\s*\d{4,9}[-/]\s*\d{0,4}\b',
    re.IGNORECASE,
)

# Para cada documento:
#   1. Concatenar texto de sus chunks parent
#   2. Extraer todos los case numbers mencionados
#   3. Para cada referencia, buscar si existe en documents.case_number
#   4. INSERT INTO citations (source_id, target_id, citation_type)
#      VALUES (%s, %s, 'explicit_reference')
#      ON CONFLICT DO NOTHING
```

**Uso en RAG:** cuando se recupera un doc, también recuperar los docs que cita
(1 hop) y añadirlos al contexto con score reducido (RRF × 0.5).

**Impacto estimado:** mejora edge_case (actualmente 100% HR@5 pero respuestas
más ricas con contexto cruzado). Habilita preguntas como:
- "¿Qué tribunal revisó la decisión de AEPD en el caso BBVA?"
- "¿Hay jurisprudencia posterior al caso Vodafone PS/00059/2020?"

**Verificación:**
```bash
PYTHONUTF8=1 python db/build_citations.py --dry-run
# Muestra pares (source, target) encontrados sin insertar

PYTHONUTF8=1 python db/build_citations.py
# Inserta en DB

# Ver grafo resultante:
PYTHONUTF8=1 python db/dbinspect.py stats
# Debe mostrar N filas en citations
```

---

### PRIO 4 — Summaries temáticos por artículo GDPR [~1h]

**Qué:** para cada artículo GDPR presente en la DB, generar un párrafo narrativo
que resuma el patrón de enforcement: sectores afectados, tipos de infracción,
multas típicas, tendencias. ~33 llamadas LLM → coste ~$0.33.

**Dónde:** nuevo script `db/build_article_summaries.py` que añade una sección
`article_summaries` a `data/corpus_index.json`.

**Por qué:** actualmente las preguntas de tipo `compliance_advice` (¿qué debo tener
en cuenta para Art.22?) fallan porque requieren síntesis de múltiples documentos.
Con los summaries temáticos en el corpus_index, el system prompt tiene el resumen
pre-generado y el LLM puede responder con fundamento sin alucinación.

**Estructura en corpus_index.json:**
```json
{
  "article_summaries": {
    "Article 32": {
      "n_cases": 47,
      "summary": "Article 32 (security of processing) violations typically involve...",
      "top_sectors": ["Banking", "Healthcare", "Retail"],
      "fine_range": {"min": 10000, "max": 3500000},
      "top_authorities": ["AEPD", "APD/GBA"]
    },
    "Article 6": { ... }
  }
}
```

**Prompt para generar cada summary:**
```
You are analyzing GDPR enforcement data. Based on these {n} decisions involving
Article {X}, write 3-4 sentences describing:
1. What types of violations typically trigger Article {X} enforcement
2. Which sectors are most affected
3. What fine ranges are typical
4. Any notable trends or precedents

Cases: [top 15 casos por artículo con título, autoridad, multa, facts snippet]
```

**Impacto estimado:** habilita compliance_advice queries (categoría actualmente
en 0%). Mejora Answer Relevance en preguntas conceptuales.

**Verificación:**
```bash
PYTHONUTF8=1 python db/build_article_summaries.py --dry-run
# Muestra qué artículos procesaría y coste estimado

PYTHONUTF8=1 python db/build_article_summaries.py
# Genera summaries y actualiza corpus_index.json

# Test:
PYTHONUTF8=1 python db/rag.py \
  --query "What do I need to consider for Article 32 compliance in a bank?" \
  --user-id test
```

---

### PRIO 5 — UI polish y demo flow [~2h]

**Objetivo:** que la demo del hackathon sea fluida y muestre claramente las
capacidades diferenciadoras.

#### Mejoras en `ui/catalog.py`

**1. Progress indicators granulares en Research tab:**
```python
# En lugar de un spinner genérico, mostrar estados:
status_ph = st.empty()
status_ph.info("🔍 Buscando en base de datos local...")
# ... tras retrieve ...
if evidence_score < 0.65:
    status_ph.warning("⚠️ Contexto insuficiente. Consultando GDPRhub...")
# ... tras external search ...
if new_docs_ingested:
    status_ph.success(f"📥 {len(new_docs_ingested)} caso(s) añadido(s) a la DB")
status_ph.info("✍️ Generando respuesta...")
```

**2. Panel de Citations con grafo de citas:**
```python
# Cuando el doc tiene citations en DB, mostrar referencias cruzadas:
if citations_graph:
    with st.expander("Referencias cruzadas"):
        for source, target in citations_graph:
            st.caption(f"→ {target['title']} ({target['year']})")
```

**3. Evidence score visible:**
```python
st.caption(f"Evidence quality: {evidence_score:.0%} | "
           f"Source: {'DB' if evidence_score >= 0.65 else 'DB + GDPRhub'}")
```

**4. Contador de docs en DB (dinámico):**
```python
# En sidebar, actualizar en cada sesión:
n_docs = cur.execute("SELECT count(*) FROM documents").fetchone()[0]
st.sidebar.metric("Casos en base de datos", f"{n_docs:,}")
# Si auto-ingest activo, se ve crecer en tiempo real durante la demo
```

---

## Timeline detallado

```
DÍA 1 — 6 agosto (hoy)
├── PRIO 1: Metadatos en contexto        ~1h
│   └── eval rápido --llm               ~45m
│       Target: faithfulness >68%
└── PRIO 3: build_citations.py           ~2h
    └── verificar grafo en DB            ~15m

DÍA 2 — 7 agosto
├── PRIO 2a: Evidence Gate               ~2h
│   └── test manual evidence scores      ~30m
└── PRIO 2b+c: GDPRhub fallback         ~2h
    └── test auto-ingest end-to-end      ~30m

DÍA 3 — 8 agosto
├── PRIO 4: Article summaries            ~1h
├── PRIO 5: UI polish                    ~2h
└── Eval completo con LLM               ~45m
    Target: faithfulness >80%, HR@5 >90%

DÍA 4 — 9 agosto
├── Ajustes post-eval
├── Rehearsal demo (15 min)
└── Commit release candidate

DÍAS 5-12 — buffer
├── Cualquier regresión
├── README + documentación hackathon
└── Vídeo demo (si requerido)
```

---

## Targets tras las 5 mejoras

| Métrica | Baseline (day4) | Tras PRIO 1 | Tras PRIO 2 | Tras PRIO 3+4 |
|---|---|---|---|---|
| HR@5 | 84.2% | 84.2% | **>90%** | **>90%** |
| MRR | 0.851 | 0.851 | **>0.90** | **>0.92** |
| Faithfulness | 62.9% | ~70% | **~82%** | **~85%** |
| Misses retrieval | 3/19 | 3/19 | **≤1/19** | **≤1/19** |
| Self-improving | No | No | **Sí** | **Sí** |
| Multi-hop queries | No | No | No | **Sí** |
| Compliance advice | No | No | No | **Sí** |

---

## Archivos a crear/modificar

| Archivo | Tipo | PRIO |
|---|---|---|
| `db/rag.py` | Modificar | 1, 2 |
| `db/build_citations.py` | Crear | 3 |
| `db/build_article_summaries.py` | Crear | 4 |
| `ui/catalog.py` | Modificar | 5 |
| `data/corpus_index.json` | Regenerar | 4 |
| `eval/results_day5_*.json` | Crear | eval |

---

## Comandos de referencia

```bash
# Cargar .env
export $(grep -v '^#' .env | xargs)

# Eval retrieval rápido (sin LLM, ~2 min)
PYTHONUTF8=1 python eval/run_eval.py \
  --golden eval/golden_set.json \
  --out eval/results_day5_retrieval.json

# Eval completo con LLM (~45 min, ~$0.80)
PYTHONUTF8=1 python eval/run_eval.py \
  --golden eval/golden_set.json --llm \
  --out eval/results_day5_llm.json

# Build citations
PYTHONUTF8=1 python db/build_citations.py

# Build article summaries
PYTHONUTF8=1 python db/build_article_summaries.py

# Corpus index (regenerar tras summaries)
PYTHONUTF8=1 python db/build_corpus_index.py

# UI
PYTHONUTF8=1 streamlit run ui/catalog.py --server.port 8501

# Test evidence gate manual
PYTHONUTF8=1 python db/rag.py \
  --query "What did AEPD decide in case EXP202213437?" \
  --user-id test
```

---

## Diferenciadores para el hackathon

1. **Auto-mejora con uso:** cada consulta fallida ingesta un doc nuevo. La DB
   crece durante la demo — demostrable en vivo con el contador de docs en sidebar.

2. **Transparencia de fuentes:** el abogado ve exactamente de dónde viene cada
   afirmación (chunk source, evidence score, si vino de DB o de GDPRhub externo).

3. **Abstención estructurada:** cuando no hay información suficiente en ninguna
   fuente, el sistema lo dice explícitamente con los casos más cercanos — nunca alucina.

4. **Grafo de citas:** referencias cruzadas entre decisiones — funcionalidad que
   ningún RAG estático puede ofrecer.

5. **Memoria persistente cross-session:** ya implementada en `user_memory` —
   el sistema recuerda las áreas de interés del usuario entre sesiones.
