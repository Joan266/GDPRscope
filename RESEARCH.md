# JurisMind — Research Notes

Investigación realizada: 27 jul 2026

---

## 1. Fuentes de datos — acceso confirmado

### GDPR Enforcement Tracker ✅ Lista para ingestar
- **URL:** https://www.enforcementtracker.com/
- **Acceso:** JSON embebido en el HTML de la página principal. No hay API oficial.
- **Volumen:** 3.202 casos (actualización horaria)
- **Campos:**
  ```json
  {
    "e": 1,               // ID secuencial
    "C": "Austria",       // País
    "a": "Austrian DPA",  // Autoridad
    "d": "2018-12-09",    // Fecha decisión
    "f": 4800,            // Multa en EUR
    "p": "Betting place", // Empresa/controlador
    "s": "Industry and Commerce", // Sector
    "r": "Art. 13 GDPR",  // Artículos infringidos
    "t": "Insufficient fulfilment of information obligations", // Tipo infracción
    "u": "https://..."    // URL documento oficial
  }
  ```
- **Estrategia ingestión:** Scraping HTML → extraer JSON → CockroachDB

### GDPRhub (MediaWiki API) ✅ Lista para ingestar
- **URL:** https://gdprhub.eu/
- **Acceso:** API MediaWiki pública. Sin registro ni API key.
- **Volumen:** 4.500+ decisiones
- **Endpoints útiles:**
  ```
  # Buscar decisiones
  GET https://gdprhub.eu/api.php?action=query&list=search&srsearch=GDPR+decision&srlimit=50&format=json

  # Obtener contenido de una decisión
  GET https://gdprhub.eu/api.php?action=parse&page=APD%2FGBA+(Belgium)+-+81%2F2020&prop=wikitext&format=json
  ```
- **Estructura por página** (template `DPAdecisionBOX`):
  ```
  Jurisdiction, DPA_Abbrevation, DPA_With_Country
  Case_Number_Name, ECLI, Type, Outcome
  Date_Decided, Date_Published, Year
  Fine, Currency
  GDPR_Articles (múltiples)
  Parties (complainant + defendant)
  Original_Source_Name_1, Original_Source_Link_1, Original_Source_Language_1
  Appeal_To_Status
  ```
- **Estrategia ingestión:** Search API paginada → por cada title, fetch parse API → parsear DPAdecisionBOX

### EUR-Lex CELLAR API ⚠️ Funciona, filtro GDPR pendiente
- **SPARQL endpoint:** https://publications.europa.eu/webapi/rdf/sparql
- **Formato respuesta:** JSON (SPARQL results)
- **Estado:** Devuelve sentencias TJUE. Filtro por GDPR (CELEX 32016R0679) no funcionó — requiere refinamiento.
- **Uso:** Corpus secundario para sentencias judiciales TJUE. Segunda fase.
- **Campos disponibles:** work (URI CELLAR), date, celex

---

## 2. Estándares oficiales de metadatos para jurisprudencia

### ECLI — European Case Law Identifier
Identificador oficial EU para decisiones judiciales.
```
ECLI:[país]:[tribunal]:[año]:[identificador]
ECLI:EU:C:2023:123   → TJUE
ECLI:ES:AN:2021:456  → Audiencia Nacional España
```
- Las decisiones **judiciales** tienen ECLI.
- Las decisiones **administrativas DPA** normalmente NO tienen ECLI (son administrativas, no judiciales).
- El schema debe soportar ECLI como campo opcional/nullable.

### ELI — European Legislation Identifier
Para legislación, no jurisprudencia. No aplica directamente.

### Akoma Ntoso
- Estándar OASIS para estructura interna de documentos legales.
- EUR-Lex lo usa internamente.
- Define: cuerpo, preámbulo, artículos, citas, metadata.
- Relevante para cuando procesemos PDFs de decisiones completas.

### Implicación para el schema
El modelo de metadatos canónico (ECLI + campos EDPB/GDPRhub) debe guiar el diseño de la tabla `documents` desde el principio. No inventar campos propios si hay un estándar que ya los define.

---

## 3. CockroachDB vs Aurora — diferencia real

| | CockroachDB C-SPANN | Aurora + pgvector |
|---|---|---|
| Vector index | Distribuido, cualquier nodo sirve queries | Regional, coordinación central |
| ACID | Multi-región nativo | Single-region; cross-region eventual |
| RAM | No requiere vector cache en memoria | pgvector necesita RAM para índice |
| Sistemas | Vector + relacional + ACID = 1 transacción | Necesitas coordinar 2 sistemas |
| Escala horizontal | Sin degradación del índice | Principalmente vertical |

### Argumento técnico para el hackathon
Una consulta normal del agente necesita:
1. Buscar vectorialmente en corpus de decisiones (C-SPANN)
2. Leer memoria del usuario (tabla relacional)
3. Filtrar por país/artículo/fecha (tabla relacional)
4. Escribir nuevo contexto (transacción)

Todo en **una sola transacción ACID**. Con Aurora + pgvector necesitas coordinar 2 sistemas o aceptar eventual consistency en la memoria del agente.

---

## 4. Estado del arte — RAG para documentos legales

### 4.1 GraphRAG / LegalGraphRAG (vanguardia, mayo 2025)

Paper clave: **LegalGraphRAG** (arxiv 2605.28120)
> "A flat knowledge graph cannot adequately differentiate between factual details, applied rules, and abstract principles, limiting accurate retrieval."

Arquitectura propuesta (multi-agente):
- **Researcher** — recupera evidencia candidata del grafo
- **Auditor** — verifica la evidencia contra el documento fuente original
- **Adjudicator** — sintetiza respuesta final con evidencia verificada

Por qué el grafo es fundamental en jurisprudencia:
- Decisiones citan otras decisiones → aristas del grafo
- GDPR artículos citados por múltiples decisiones → nodos compartidos
- "¿Cómo trata la AEPD el Art. 6?" = traversal de grafo, no solo similarity search
- Análisis cross-jurisdiccional = comparar subgrafos por país/DPA

Paper complementario: **Graph RAG for Legal Norms: Hierarchical and Temporal** (arxiv 2505.00039)
- Enfatiza la dimensión temporal: las normas se modifican, se derogan, se sustituyen
- El grafo debe tener timestamps y relaciones "amends", "supersedes", "guides"

### 4.2 Hybrid Search — el estándar actual

```
Query usuario
    ↓
BM25 (keyword: nombres de partes, artículos exactos, términos específicos)
    +
Vector search C-SPANN (semántica: "transferencia sin base legal")
    ↓ [Reciprocal Rank Fusion — RRF]
Top-K candidatos mezclados
    ↓
Cross-encoder reranker
    ↓
Respuesta con citación obligatoria + link directo
```

Resultados medidos (LegalBench-RAG + otros benchmarks):
- Hybrid + reranking: correlación **0.92** con relevancia real
- Solo vector: **0.75**
- Recall@10: hybrid **96%** vs single-query **78%**

### 4.3 Query Expansion
Generar 3 sub-queries por pregunta del usuario antes de buscar.
- Lift de recall@10: 78% → 96%
- Coste: triplica las llamadas de embedding (asumible en este volumen)

### 4.4 Chunking para documentos legales
- Chunks de ~500 tokens con 20% overlap
- Prepend del resumen del documento a cada chunk (mejora BM25 en nombres de partes)
- Chunking jerárquico: resumen documento → resumen sección → chunk

---

## 5. Diseño de base de datos — consideraciones clave

### Por qué el schema importa desde el día 1
- Cambio de modelo de embedding → re-embebido masivo sin perder documentos
- Decisión apelada y revocada → status, superseded_by deben existir antes de que pase
- Limpieza/actualización masiva → sin flags correctos es un trabajazo enorme
- Volumen creciente indefinidamente → diseñar para escala desde el principio

### Tablas necesarias (diseño pendiente)

```
documents          — metadatos canónicos de cada decisión
chunks             — fragmentos para vector search (FK → documents)
citations          — grafo de citaciones entre documentos (FK → documents × 2)
user_memory        — memoria cross-session por usuario
research_sessions  — historial de sesiones de investigación
```

### Campos críticos que deben existir desde el principio

**documents:**
- `ecli` (nullable — solo decisiones judiciales)
- `source` (gdprhub | enforcement_tracker | edpb | eur_lex)
- `source_id` (ID en la fuente original)
- `status` (active | superseded | appealed | overturned)
- `superseded_by` (FK nullable → documents)
- `language_original`
- `ingested_at`, `updated_at`
- `processing_status` (pending | processing | embedded | failed)

**chunks:**
- `embedding_model` — versión del modelo usado
- `embedding_version` — para poder re-embeder sin perder datos
- `chunk_index` — posición en el documento
- `is_current` — false si el chunk pertenece a una versión anterior del embedding

**citations:**
- `from_document_id` → `to_document_id`
- `citation_type` (cites | supersedes | amends | guides)
- `confidence` (extraído manualmente o con LLM)

---

## 6. Mercado — contexto validado

- Mercado privacy software: $7.5B en 2026 → $60B en 2034 (CAGR ~30%)
- GDPR fines 2025: €1.2B, 443 notificaciones diarias
- Harvey AI: ~$1.200/seat, sin corpus DPA europeo
- Paxton AI: $159/mes — competidor real aunque no especializado GDPR
- Gap confirmado: ninguna herramienta <$200/mes combina corpus DPA + búsqueda semántica + memoria

---

## 7. Scope hackathon vs producto real

### Lo que entra en 22 días
- Schema CockroachDB completo (preparado para grafo, aunque no se use)
- Ingestión: Enforcement Tracker + GDPRhub (~500-800 decisiones iniciales)
- Hybrid search: BM25 + C-SPANN (sin GraphRAG completo)
- Capa de memoria: user_memory con transacción ACID
- API FastAPI: /search, /memory, /synthesize
- UI mínima de chat + panel de memoria

### Lo que queda para después
- GraphRAG completo (tabla citations poblada + traversal)
- Query expansion automática
- Cross-encoder reranker
- Multi-idioma / traducción automática
- Ingestión EUR-Lex (corpus TJUE)

---

## Fuentes

- [LegalGraphRAG — arxiv 2605.28120](https://arxiv.org/abs/2605.28120)
- [Graph RAG for Legal Norms — arxiv 2505.00039](https://arxiv.org/html/2505.00039v1)
- [CockroachDB C-SPANN Distributed Vector Indexing](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/)
- [CockroachDB vs Amazon Aurora](https://www.cockroachlabs.com/compare/amazon-aurora-vs-cockroachdb/)
- [Hybrid Search for RAG 2026](https://denser.ai/blog/hybrid-search-for-rag/)
- [Towards Reliable Retrieval in RAG for Legal Datasets](https://arxiv.org/pdf/2510.06999)
- [LegalBench-RAG Benchmark](https://arxiv.org/pdf/2408.10343)
- [Akoma Ntoso OASIS Standard](https://www.oasis-open.org/standard/akn-v1-0/)
- [GDPR Fines €7.1B — Kiteworks 2026](https://www.kiteworks.com/gdpr-compliance/gdpr-fines-data-privacy-enforcement-2026/)
- [CockroachDB × AWS Hackathon Rules](https://cockroachdb-ai.devpost.com/rules)
