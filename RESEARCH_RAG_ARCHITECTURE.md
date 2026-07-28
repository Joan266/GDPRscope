# JurisMind — RAG Architecture Research

Investigación realizada: 27 jul 2026

---

## 1. ECLI — ¿cubre decisiones DPA?

**No.** ECLI (European Case Law Identifier) es exclusivamente para decisiones **judiciales**. Las decisiones DPA son actos administrativos y no tienen ECLI.

| Tipo de documento | ECLI | Identificador real |
|---|---|---|
| Sentencia TJUE | Sí | `ECLI:EU:C:2023:123` |
| Sentencia tribunal nacional | Sí | `ECLI:ES:AN:2021:456` |
| Decisión AEPD, CNIL, ICO... | **No** | Número expediente interno |
| Decisión APD Bélgica | **No** | "APD/GBA (Belgium) - 81/2020" |

**Implicación para el schema:**
- Campo `ecli` nullable (solo para documentos judiciales)
- Campo `source_identifier` propio para decisiones DPA (esquema GDPRhub como de facto estándar)
- Campo `document_type`: `dpa_decision` | `court_judgment` | `guidance`

---

## 2. LegalGraphRAG — Paper A (arxiv 2605.28120, mayo 2025)

**Dominio:** Jurisprudencia (decisiones y sentencias). El más relevante para JurisMind.

### Problema que resuelve
RAG plano trata cada chunk como independiente. Una decisión DPA belga y una alemana pueden citar el mismo artículo GDPR, pero el sistema no sabe que están conectadas. Se pierde el razonamiento cruzado que es precisamente el valor de la herramienta.

### Arquitectura del grafo — 3 capas

```
FACT GRAPH (G_fac)
  Nodos:  Cases, Articles, Offenses
  Aristas:
    Case  →  cita       →  Article
    Case  →  condena    →  Offense
  Propósito: red de precedentes verificados

ONTOLOGY GRAPH (G_ont)
  Nodos:  Communities (grupos semánticos de casos similares)
  Dimensiones semánticas agrupadas:
    - Atributos del demandado
    - Comportamientos / infracciones
    - Características de la víctima / afectado
    - Estados mentales / intencionalidad
  Algoritmo: k-NN + Leiden community detection
  Propósito: recuperación contextual por similitud temática

RULE GRAPH (G_rul)
  Nodos:  Articles + Judicial Interpretations + Diagnostic Checklists
  Aristas: Article  →  resuelve ambigüedad vía  →  Checklist de verificación
  Propósito: distinguir condiciones legales sutiles entre artículos parecidos
             (ej: Art. 6.1.a vs Art. 6.1.b — ambos sobre licitud, distintos requisitos)
```

### Sistema multi-agente

```
RESEARCHER
  Tres operadores de recuperación en paralelo:
  ├── Semantic Match       — similitud ontológica directa
  ├── Community Expansion  — recupera casos de la misma comunidad temática
  └── Charge-Anchored      — conecta evidencia vinculada a los cargos inferidos
              ↓
AUDITOR
  Valida cada pieza de evidencia contra los Diagnostic Checklists
  Filtra evidencia inaplicable
  Construye subgrafo verificado
              ↓
ADJUDICATOR
  Sintetiza respuesta final
  Incluye citas explícitas y trazables a fuentes originales
```

### Resultados medidos (benchmark CAIL2018)
- LegalGraphRAG: **49.5%** de precisión promedio
- HippoRAG2 (segundo mejor): 43.1%
- Mejora sobre baselines GraphRAG: **6.3% – 19.1%**
- Métrica clave: *Traceable Correct* — respuestas correctas con evidencia verificable en el contexto recuperado. Convierte la caja negra en razonamiento auditable.

---

## 3. Graph RAG for Legal Norms — Paper B (arxiv 2505.00039, mayo 2025)

**Dominio:** Legislación (normas, artículos, enmiendas, evolución temporal). Relevante para modelar el GDPR como estructura de nodos.

### Problema que resuelve
El GDPR no es un documento estático. El Art. 6 tiene interpretaciones que evolucionan (Schrems II 2020, EU AI Act 2024...). RAG plano no distingue "Art. 6 en 2019" de "Art. 6 reinterpretado post-Schrems en 2021". Trata el artículo como un bloque inmutable.

### Innovaciones técnicas

#### Text Units en vez de chunks arbitrarios
No partir por tokens. Cada fragmento incluye el contexto padre completo.

```
# Chunk tradicional (problemático)
"... el tratamiento será lícito si el interesado dio su consentimiento..."

# Text Unit (correcto)
[Art. 6 GDPR — Licitud del tratamiento]
  Caput: El tratamiento solo será lícito si se cumple alguna de las siguientes condiciones:
  Inciso 1.a: el interesado dio su consentimiento para el tratamiento de sus datos...
```

El LLM recibe contexto completo, no un trozo descontextualizado.

#### Versiones como agregación, no duplicación

```
Art. 6 GDPR
  ├── Version 2018-05-25  (entrada en vigor)
  ├── Version 2021-09-16  (post-Schrems II — reinterpretación ICO/EDPB)
  └── Version 2024-xx-xx  (post-EU AI Act)

Regla: si el Inciso 1.a no cambia entre versiones → se reutiliza, no se copia
       si el Inciso 1.b cambia → nueva versión SOLO para ese sub-nodo
```

Permite consultas temporales: *"¿Qué decía el Art. 46 en enero 2020?"*
Elimina redundancia masiva en el corpus.

#### Multi-vector embeddings
En vez de un único vector por entidad:
- Vector de metadatos (propiedades del nodo)
- Vector por cada relación (cómo se conecta con otros nodos)
- Vector por versión temporal

Más puntos en el espacio vectorial → recuperación más granular y contextualizada.

#### Tipos de aristas del grafo normativo
```
Estructurales:   padre → hijo (Art → Inciso → Alínea)
Temporales:      version_anterior → version_posterior
Acciones:        text_change | repeal | suppression | renumbering
Referencias:     norm_A → cita → norm_B
Temáticas:       comunidades por título/capítulo
```

#### Segmentación semántica orientada por estructura
No segmentar por frases o párrafos arbitrarios. Usar la estructura legal del documento (Art., Inciso, Párrafo) como guía de chunking. Requiere parser especializado (EUR-Lex usa Akoma Ntoso XML para esto).

### Limitación importante del Paper B
> "Se enfoca en legislación. Carece de validación contra jurisprudencia (case law). Propone esto como trabajo futuro."

---

## 4. Cómo se combinan para JurisMind

Los dos papers modelan capas distintas que juntas forman el sistema completo:

```
CAPA LEGISLATIVA  (Paper B)
  GDPR Art. 6  →  versiones temporales  →  sub-artículos con Text Units
  GDPR Art. 13 →  versiones temporales  →  sub-artículos con Text Units
        ↓  [arista: cited_by]
CAPA JURISPRUDENCIAL  (Paper A)
  Decisión AEPD 2021  →  cita  →  Art. 6.1.a
  Decisión CNIL 2022  →  cita  →  Art. 6.1.a
  Decisión ICO  2023  →  cita  →  Art. 6.1.a
        ↓  [arista: similar_to / appeals]
  Decisión AEPD 2021  →  apelada  →  Sentencia AN 2022
```

Query posible con grafo completo:
> "¿Cómo ha evolucionado la interpretación del Art. 6.1.a en distintas DPAs europeas entre 2020 y 2024?"

Sin grafo: 4-6 horas manuales.
Con grafo: traversal + síntesis + citación en segundos.

---

## 5. Modelos de embedding — decisión

### Massive Legal Embedding Benchmark (MLEB, arxiv 2510.19365)
Benchmark específico para texto legal. Incluye dataset **GDPR Holdings Retrieval**: 500 fact patterns emparejados con decisiones regulatorias y judiciales europeas — nuestro caso de uso exacto.

**Ranking NDCG@10:**
| Posición | Modelo | Score | Disponible en Bedrock |
|---|---|---|---|
| 1 | Kanon 2 Embedder | 86.03 | No |
| 2 | **Voyage 3 Large** | **85.71** | No (Azure sí) |
| 3 | Voyage 3.5 | 84.07 | No |
| 4 | Qwen3 Embedding 8B | 82.96 | No |
| 8 | Voyage Law-2 | 79.63 | No |
| — | **Titan Text Embeddings V2** | No en MLEB | **Sí — nativo Bedrock** |

### Decisión para JurisMind
- **Hackathon:** Titan Text Embeddings V2 (8192 tokens, 1024 dims). Nativo de Bedrock, no sale del ecosistema AWS.
- **Producción:** migrar a Voyage 3 Large. Por eso `embedding_model` y `embedding_version` son campos obligatorios en la tabla chunks desde el día 1 — el re-embebido masivo tiene que ser posible sin perder datos.

---

## 6. CockroachDB — full-text search y BM25

CockroachDB **no implementa BM25 nativo**. Soporta `tsvector` (full-text search PostgreSQL estándar: `ts_rank`, `to_tsvector`, `to_tsquery`) y trigram indexes.

| Opción | Calidad BM25 | Complejidad | Decisión |
|---|---|---|---|
| `tsvector` nativo CockroachDB | Buena (no BM25 puro) | Baja | **Hackathon — usar esto** |
| BM25 manual vía PL/pgSQL | Buena | Alta | No |
| Elasticsearch vía CDC | Excelente | Muy alta | Producción futura |

El hybrid search `tsvector + C-SPANN + RRF` es perfectamente defendible en el demo. BM25 real es una mejora de producción documentada.

---

## 7. Stack de retrieval recomendado (estado del arte 2025-2026)

```
Query usuario
    ↓
[Query Expansion]  — producción futura
  LLM genera 3 sub-queries relacionadas
  Lift recall@10: 78% → 96%
    ↓
[Hybrid Retrieval]  — hackathon
  tsvector (keyword: Art. 6.1.a, nombres de partes, términos exactos)
    +
  C-SPANN vector search (semántica: "transferencia sin base legal adecuada")
    ↓ [Reciprocal Rank Fusion — RRF]
[Reranking]  — producción futura
  Cross-encoder reranker sobre top-K candidatos
  Correlación con relevancia real: 0.92 vs 0.75 (solo vector)
    ↓
[Generación con citación obligatoria]  — hackathon
  Respuesta + link directo al documento original en cada afirmación
```

---

## 8. Chunking — parent-child pattern

Hallazgo de guías de producción RAG (Orkes, Awesome-RAG-Production):

**Parent-child chunking** — especialmente útil para documentos legales largos:
- **Chunk hijo** (256-512 tokens, overlap 10-20%): se usa para retrieval preciso
- **Chunk padre** (documento completo o sección): se envía al LLM para contexto completo

Una decisión DPA puede tener 30 páginas. El chunk hijo localiza el párrafo relevante; el LLM necesita el contexto padre para sintetizar correctamente.

**Metadata obligatoria por chunk** (estándar de producción):
- `source_document` — FK al documento padre
- `section_heading` — encabezado de sección para BM25 y display
- `parent_chunk_id` — FK al chunk padre si aplica
- `token_count` — control de tamaño
- `chunk_index` — posición en el documento

---

## 9. Implicaciones concretas para el schema CockroachDB

Lo que DEBE existir desde el día 1 aunque no se use todo:

```sql
-- documents: campos críticos para largo plazo
ecli                VARCHAR   NULLABLE  -- solo decisiones judiciales
source_identifier   VARCHAR   NOT NULL  -- ID en fuente original (GDPRhub, tracker...)
document_type       ENUM      NOT NULL  -- dpa_decision | court_judgment | guidance
status              ENUM      NOT NULL  -- active | superseded | appealed | overturned
superseded_by       UUID      NULLABLE  -- FK → documents
version_date        DATE      NULLABLE  -- para capa legislativa (Art. GDPR con versiones)
language_original   VARCHAR   NOT NULL
processing_status   ENUM      NOT NULL  -- pending | processing | embedded | failed
ingested_at         TIMESTAMP NOT NULL
updated_at          TIMESTAMP NOT NULL

-- chunks: campos críticos para re-embebido futuro y parent-child
embedding_model     VARCHAR   NOT NULL  -- "titan-embed-v2", "voyage-3-large"...
embedding_version   INTEGER   NOT NULL  -- re-embeber sin perder datos
chunk_index         INTEGER   NOT NULL  -- posición en el documento
is_current          BOOLEAN   NOT NULL  -- false si pertenece a versión anterior de embedding
text_unit           TEXT      NOT NULL  -- Text Unit con contexto padre incluido
parent_chunk_id     UUID      NULLABLE  -- FK → chunks (parent-child pattern)
section_heading     VARCHAR   NULLABLE  -- encabezado de sección
token_count         INTEGER   NOT NULL  -- control de tamaño

-- citations: grafo de relaciones entre documentos (poblada en producción)
from_document_id    UUID      NOT NULL  -- FK → documents
to_document_id      UUID      NOT NULL  -- FK → documents
citation_type       ENUM      NOT NULL  -- cites | supersedes | amends | appeals | guides
confidence          FLOAT     NOT NULL  -- 1.0 si manual, <1.0 si extraído por LLM
extracted_by        VARCHAR   NOT NULL  -- "manual" | "llm-extraction"
```

### Qué entra en 22 días vs después

| Componente | Hackathon | Producción |
|---|---|---|
| Schema completo (todas las tablas) | Sí | — |
| Ingestión Enforcement Tracker + GDPRhub | Sí | + EUR-Lex |
| Text Units + parent-child chunking | Sí | — |
| Hybrid search tsvector + C-SPANN + RRF | Sí | → BM25 real |
| Embedding: Titan Text Embeddings V2 | Sí | → Voyage 3 Large |
| Memoria cross-session (user_memory) | Sí | — |
| Citations table poblada | No (existe vacía) | Sí |
| GraphRAG traversal | No | Sí |
| Query expansion | No | Sí |
| Cross-encoder reranker | No | Sí |
| Multi-vector embeddings | No | Sí |
| Capa legislativa (Art. GDPR con versiones) | No | Sí |

---

## Fuentes

- [LegalGraphRAG — arxiv 2605.28120](https://arxiv.org/abs/2605.28120)
- [Graph RAG for Legal Norms: Hierarchical and Temporal — arxiv 2505.00039](https://arxiv.org/html/2505.00039v1)
- [Massive Legal Embedding Benchmark (MLEB) — arxiv 2510.19365](https://arxiv.org/html/2510.19365v1)
- [Voyage Law-2 benchmark — Voyage AI](https://blog.voyageai.com/2024/04/15/domain-specific-embeddings-and-retrieval-legal-edition-voyage-law-2/)
- [AWS Bedrock Embedding Models 2026](https://qualixsolutions.com/blog/best-aws-bedrock-embedding-models/)
- [CockroachDB Full-Text Search](https://www.cockroachlabs.com/docs/stable/full-text-search)
- [European Case Law Identifier — e-Justice Portal EU](https://e-justice.europa.eu/topics/legislation-and-case-law/european-case-law-identifier-ecli_en)
- [Hybrid Search for RAG 2026](https://denser.ai/blog/hybrid-search-for-rag/)
- [LegalBench-RAG Benchmark](https://arxiv.org/pdf/2408.10343)
- [Towards Reliable Retrieval in RAG for Large Legal Datasets](https://arxiv.org/pdf/2510.06999)
- [Awesome-RAG-Production — GitHub](https://github.com/Yigtwxx/Awesome-RAG-Production)
