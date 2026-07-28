-- =============================================================
-- JurisMind — CockroachDB Schema
-- v1.0.0 — 2026-07-28
--
-- 5 tablas:
--   1. documents       — metadatos canónicos de cada decisión/sentencia
--   2. chunks          — fragmentos para vector search (patrón parent-child)
--   3. citations       — grafo de citas entre documentos (preparado para GraphRAG)
--   4. user_memory     — memoria persistente cross-session por usuario
--   5. research_sessions — historial de sesiones de investigación
--
-- Fuentes de datos:
--   - GDPRhub (MediaWiki API)         → 4.500+ decisiones DPA + tribunales nacionales
--   - EUR-Lex CELLAR (SPARQL)         → sentencias TJUE (CJEU)
--   - GDPR Enforcement Tracker        → 3.202 casos con multas
--
-- Notas CockroachDB:
--   - VECTOR(1024): Amazon Titan Text Embeddings V2 (1024 dims por defecto)
--   - CREATE VECTOR INDEX: C-SPANN (GA dic 2025)
--   - INVERTED INDEX: para TEXT[], JSONB y TSVECTOR
--   - gen_random_uuid(): UUIDs distribuidos seguros
-- =============================================================


-- =============================================================
-- TABLE 1: documents
-- Registro canónico de cada decisión o sentencia.
-- Una fila = un documento legal.
-- Fuentes: gdprhub | eurlex | enforcement_tracker
-- =============================================================

CREATE TABLE IF NOT EXISTS documents (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ── Origen ────────────────────────────────────────────────
    source              TEXT            NOT NULL,
    -- 'gdprhub' | 'eurlex' | 'enforcement_tracker'

    source_id           TEXT            NOT NULL,
    -- GDPRhub  → título de la página: "CNIL (France) - SAN-2021-003"
    -- EUR-Lex  → CELEX: "62018CJ0311"
    -- Tracker  → ID secuencial del tracker: "1"

    document_type       TEXT            NOT NULL,
    -- 'dpa_decision'   → decisión de autoridad administrativa (CNIL, AEPD, APD...)
    -- 'court_judgment' → sentencia tribunal nacional que aplica GDPR
    -- 'cjeu_judgment'  → sentencia del TJUE (Schrems II, Planet49...)

    pipeline_version    TEXT            NOT NULL DEFAULT '1.0.0',
    -- permite detectar y re-ingestar registros cuando cambia el pipeline

    -- ── Identificadores legales ────────────────────────────────
    title               TEXT            NOT NULL,
    case_number         TEXT,
    ecli                TEXT,
    -- ECLI solo en decisiones judiciales (tribunales + TJUE)
    -- DPA decisions NO tienen ECLI
    celex               TEXT,
    -- Solo documentos EUR-Lex: "62018CJ0311"

    -- ── Jurisdicción y autoridad ───────────────────────────────
    jurisdiction        TEXT,           -- 'France' | 'Germany' | 'EU' | ...
    authority           TEXT,           -- nombre completo
    authority_abbrev    TEXT,           -- 'CNIL' | 'APD/GBA' | 'KG Berlin' | ...

    -- ── Fechas ────────────────────────────────────────────────
    decision_date       DATE,           -- NULL si solo se conoce el año
    decision_year       SMALLINT,       -- siempre intentar poblarlo (tracker: campo y; GDPRhub: Year)
    date_published      DATE,
    date_started        DATE,           -- inicio del caso (GDPRhub: Date_Started, presente en 31%)

    -- ── Resultado ─────────────────────────────────────────────
    case_type           TEXT,           -- 'Complaint' | 'Investigation' | 'Own Initiative'
    outcome             TEXT,           -- 'Violation Found' | 'Upheld' | 'Rejected' | 'Other Outcome'

    -- ── Sanción ───────────────────────────────────────────────
    fine_amount         BIGINT,         -- EUR enteros; NULL si no hay multa (advertencia, orientación)
    fine_currency       TEXT,           -- 'EUR' | 'GBP' | ... (relevante para UK post-Brexit)

    -- ── Sector e implicado ────────────────────────────────────
    sector              TEXT,           -- del Enforcement Tracker: "Industry and Commerce" | "Health" ...
    controller_name     TEXT,           -- empresa/organismo controlador (tracker: p; GDPRhub: Party_Name_1)

    -- ── Arrays y objetos estructurados ────────────────────────
    gdpr_articles       TEXT[],
    -- Normalizado a array en ingesta.
    -- GDPRhub: campos GDPR_Article_1..9 → array
    -- Tracker: string "Art. 5 GDPR, Art. 13 GDPR" → split → array
    -- Ejemplo: ['Article 5 GDPR', 'Article 17 GDPR', 'Article 83 GDPR']

    parties             JSONB,
    -- [{name: "Deutsche Wohnen SE", url: "https://...", role?: "controller"}, ...]
    -- GDPRhub: Party_Name_1..5 + Party_Link_1..5

    source_urls         JSONB,
    -- [{url: "https://...", language: "German", language_code: "DE", name: "Legifrance"}, ...]
    -- GDPRhub: Original_Source_Name/Link/Language_1..3

    national_laws       JSONB,
    -- [{name: "§ 30 OWiG", url: "https://..."}, ...]
    -- GDPRhub: National_Law_Name/Link_1..5

    eu_laws             JSONB,
    -- [{name: "Article 101 TFEU", url: "https://..."}, ...]
    -- GDPRhub: EU_Law_Name/Link_1..2

    appeal_chain        JSONB,
    -- {
    --   from: {body: "LG Berlin", case_number: "...", status: "Upheld", url: "..."},
    --   to:   {body: "BGH",       case_number: null,  status: "Unknown", url: null}
    -- }

    -- ── Texto semántico ────────────────────────────────────────
    summary_teaser      TEXT,           -- 1 párrafo. GDPRhub: primera línea tras el template
    summary_facts       TEXT,           -- GDPRhub: sección "=== Facts ==="
    summary_dispute     TEXT,           -- GDPRhub: sección "=== Dispute ===" (presente en ~43%)
    summary_holding     TEXT,           -- GDPRhub: sección "=== Holding ==="
    full_text           TEXT,           -- texto completo si disponible (EUR-Lex PDFs, EDPB)
    full_text_language  TEXT,           -- idioma del full_text ('en' | 'fr' | 'de' | ...)

    -- ── Full-text search ──────────────────────────────────────
    search_vector       TSVECTOR,
    -- Poblado en ingesta:
    -- to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary_teaser,'')
    --             || ' ' || coalesce(summary_facts,'') || ' ' || coalesce(summary_holding,''))

    -- ── Control de ingesta ────────────────────────────────────
    ingested_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT uq_documents_source     UNIQUE (source, source_id),
    CONSTRAINT chk_documents_type      CHECK  (document_type IN ('dpa_decision', 'court_judgment', 'cjeu_judgment')),
    CONSTRAINT chk_documents_fine      CHECK  (fine_amount IS NULL OR fine_amount >= 0)
);

CREATE INDEX          idx_doc_jurisdiction  ON documents (jurisdiction);
CREATE INDEX          idx_doc_year          ON documents (decision_year DESC);
CREATE INDEX          idx_doc_source        ON documents (source, document_type);
CREATE INDEX          idx_doc_fine          ON documents (fine_amount DESC) WHERE fine_amount IS NOT NULL;
CREATE INDEX          idx_doc_updated       ON documents (updated_at DESC);
CREATE INVERTED INDEX idx_doc_articles      ON documents (gdpr_articles);
CREATE INVERTED INDEX idx_doc_search        ON documents (search_vector);


-- =============================================================
-- TABLE 2: chunks
-- Fragmentos de texto para vector search.
-- Patrón parent-child:
--   chunk 'parent' → texto largo (500-800 tokens) → enviado al LLM como contexto
--   chunk 'child'  → texto corto (100-150 tokens)  → usado para recuperación por embedding
--
-- Un documento genera:
--   1 parent por sección (facts, holding, ...)
--   N children por parent (solapados ~20%)
-- =============================================================

CREATE TABLE IF NOT EXISTS chunks (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID            NOT NULL REFERENCES documents (id) ON DELETE CASCADE,

    -- ── Estructura parent-child ───────────────────────────────
    chunk_type          TEXT            NOT NULL,   -- 'parent' | 'child'
    parent_id           UUID            REFERENCES chunks (id) ON DELETE CASCADE,
    -- NULL para chunks padre; apunta al padre para chunks hijo

    chunk_index         INT             NOT NULL,   -- orden dentro del documento (0-based)

    -- ── Contenido ─────────────────────────────────────────────
    content             TEXT            NOT NULL,
    content_tokens      INT,
    -- estimación de tokens (len(content) / 4 como heurística inicial)
    -- útil para no superar context window del LLM

    section             TEXT,
    -- 'teaser' | 'facts' | 'dispute' | 'holding' | 'full_text'
    -- permite filtrar por sección en retrieval

    -- ── Embedding ─────────────────────────────────────────────
    embedding           VECTOR(1024),
    -- Amazon Titan Text Embeddings V2: 1024 dimensiones (default)
    -- NULL hasta que el pipeline de embedding lo procese

    embedding_model     TEXT,           -- 'amazon.titan-embed-text-v2:0'
    embedding_version   TEXT,           -- tag de versión para detectar re-embeddings necesarios
    embedded_at         TIMESTAMPTZ,    -- cuándo se generó el embedding

    -- ── Full-text search (hybrid retrieval) ───────────────────
    search_vector       TSVECTOR,
    -- permite combinar vector search + BM25 via RRF (Reciprocal Rank Fusion)

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT chk_chunks_type    CHECK (chunk_type IN ('parent', 'child')),
    CONSTRAINT chk_chunks_parent  CHECK (
        (chunk_type = 'parent' AND parent_id IS NULL) OR
        (chunk_type = 'child'  AND parent_id IS NOT NULL)
    )
);

CREATE INDEX          idx_chunks_document     ON chunks (document_id, chunk_index);
CREATE INDEX          idx_chunks_parent       ON chunks (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX          idx_chunks_section      ON chunks (document_id, section);
CREATE INDEX          idx_chunks_unembedded   ON chunks (created_at) WHERE embedding IS NULL;
-- ↑ permite al pipeline encontrar rápido qué chunks pendientes de embeber
CREATE INVERTED INDEX idx_chunks_search       ON chunks (search_vector);

-- Vector index C-SPANN para approximate nearest neighbor search
-- Solo sobre chunks con embedding ya calculado
CREATE VECTOR INDEX   idx_chunks_embedding    ON chunks (embedding) WHERE embedding IS NOT NULL;


-- =============================================================
-- TABLE 3: citations
-- Grafo de citas entre documentos.
-- En v1.0 se puebla desde:
--   - EUR-Lex SPARQL (cdm:work_cites_work) → método 'sparql'
--   - Extracción manual/NLP del texto       → método 'nlp'
-- La tabla existe desde el día 1 para soportar GraphRAG futuro.
-- =============================================================

CREATE TABLE IF NOT EXISTS citations (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    citing_document_id  UUID            NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    cited_document_id   UUID            NOT NULL REFERENCES documents (id) ON DELETE CASCADE,

    -- ── Tipo de relación ──────────────────────────────────────
    relation_type       TEXT            NOT NULL DEFAULT 'cites',
    -- 'cites'       → cita genérica (default)
    -- 'affirms'     → el tribunal confirma el razonamiento del caso citado
    -- 'overrules'   → el tribunal revoca el razonamiento del caso citado
    -- 'distinguishes' → el tribunal diferencia su caso del citado
    -- 'follows'     → el tribunal aplica el mismo criterio

    -- ── Contexto de la cita ───────────────────────────────────
    citation_context    TEXT,
    -- fragmento textual donde aparece la cita (hasta ~500 chars)
    -- útil para GraphRAG: saber POR QUÉ se cita

    -- ── Metadatos de extracción ───────────────────────────────
    extraction_method   TEXT            NOT NULL DEFAULT 'manual',
    -- 'sparql'  → obtenido de EUR-Lex CELLAR via cdm:work_cites_work
    -- 'nlp'     → extraído del texto por NLP/LLM
    -- 'manual'  → introducido manualmente

    confidence          FLOAT           NOT NULL DEFAULT 1.0,
    -- 1.0 para sparql y manual; 0.0-1.0 para NLP según modelo

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT uq_citations         UNIQUE  (citing_document_id, cited_document_id, relation_type),
    CONSTRAINT chk_citations_self   CHECK   (citing_document_id != cited_document_id),
    CONSTRAINT chk_citations_conf   CHECK   (confidence BETWEEN 0.0 AND 1.0),
    CONSTRAINT chk_citations_type   CHECK   (relation_type IN ('cites', 'affirms', 'overrules', 'distinguishes', 'follows'))
);

CREATE INDEX idx_citations_citing ON citations (citing_document_id);
CREATE INDEX idx_citations_cited  ON citations (cited_document_id);
CREATE INDEX idx_citations_type   ON citations (relation_type);
-- Índice para traversal bidireccional del grafo (GraphRAG futuro)
CREATE INDEX idx_citations_graph  ON citations (citing_document_id, cited_document_id, relation_type);


-- =============================================================
-- TABLE 4: user_memory
-- Memoria persistente cross-session por usuario.
-- El diferenciador clave de JurisMind vs. herramientas actuales.
--
-- Tipos de memoria:
--   'preference'      → qué jurisdicciones trabaja, estilo de análisis
--   'learned_pattern' → artículos GDPR que más usa, posiciones argumentales frecuentes
--   'client_context'  → contexto de un asunto específico (sin datos identificativos)
--   'research_note'   → conclusión que el asesor marcó explícitamente como importante
-- =============================================================

CREATE TABLE IF NOT EXISTS user_memory (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT            NOT NULL,
    -- identificador del usuario (email hasheado o UUID de auth)

    -- ── Tipo y contenido ──────────────────────────────────────
    memory_type         TEXT            NOT NULL,
    content             TEXT            NOT NULL,
    -- texto libre de la memoria: "Este cliente opera en FR y DE. Suele invocar Art. 6(1)(f)."

    context             JSONB,
    -- metadatos estructurados opcionales:
    -- {
    --   jurisdiction:    ['FR', 'DE'],
    --   gdpr_articles:   ['Article 6 GDPR'],
    --   session_id:      "uuid-de-la-sesion-origen",
    --   tags:            ['legitimate_interest', 'b2b']
    -- }

    -- ── Relevancia y ciclo de vida ────────────────────────────
    importance_score    FLOAT           NOT NULL DEFAULT 0.5,
    -- 0.0-1.0. Se sube cuando el usuario confirma la memoria,
    -- se baja por decaimiento temporal si no se accede.

    access_count        INT             NOT NULL DEFAULT 0,
    last_accessed_at    TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,    -- NULL = sin expiración

    -- ── Embedding (recuperación semántica de memorias) ────────
    embedding           VECTOR(1024),
    embedding_model     TEXT,
    embedding_version   TEXT,
    embedded_at         TIMESTAMPTZ,

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT chk_memory_type  CHECK (memory_type IN ('preference', 'learned_pattern', 'client_context', 'research_note')),
    CONSTRAINT chk_memory_score CHECK (importance_score BETWEEN 0.0 AND 1.0)
);

CREATE INDEX        idx_memory_user         ON user_memory (user_id, memory_type);
CREATE INDEX        idx_memory_importance   ON user_memory (user_id, importance_score DESC);
CREATE INDEX        idx_memory_access       ON user_memory (user_id, last_accessed_at DESC);
CREATE INDEX        idx_memory_expiry       ON user_memory (expires_at) WHERE expires_at IS NOT NULL;
CREATE VECTOR INDEX idx_memory_embedding    ON user_memory (embedding) WHERE embedding IS NOT NULL;


-- =============================================================
-- TABLE 5: research_sessions
-- Historial de cada consulta realizada por un usuario.
-- Permite:
--   - Construir memoria a partir del feedback (→ user_memory)
--   - Detectar consultas similares para mejorar retrieval
--   - Auditar qué fuentes se usaron en cada respuesta
--   - Control de costes (token_count)
-- =============================================================

CREATE TABLE IF NOT EXISTS research_sessions (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT            NOT NULL,

    -- ── Consulta ──────────────────────────────────────────────
    title               TEXT,
    -- auto-generado por el LLM o puesto por el usuario
    -- ejemplo: "Legitimidad del interés legítimo en marketing B2B"

    query               TEXT            NOT NULL,
    -- pregunta original tal como la escribió el asesor

    query_embedding     VECTOR(1024),
    -- embedding de la consulta — permite encontrar sesiones similares

    -- ── Contexto de la sesión ─────────────────────────────────
    session_context     JSONB,
    -- {
    --   client_type:  'startup' | 'enterprise' | 'public_sector',
    --   jurisdictions: ['ES', 'FR'],
    --   gdpr_articles: ['Article 6 GDPR'],
    --   tags:          ['consent', 'cookies']
    -- }

    -- ── Documentos recuperados ────────────────────────────────
    retrieved_chunks    JSONB,
    -- snapshot de qué chunks se recuperaron y usaron:
    -- [
    --   {
    --     chunk_id:          "uuid",
    --     document_id:       "uuid",
    --     document_title:    "CNIL (France) - SAN-2021-003",
    --     relevance_score:   0.87,
    --     cited_in_response: true
    --   },
    --   ...
    -- ]

    -- ── Respuesta ─────────────────────────────────────────────
    response            TEXT,
    response_model      TEXT,           -- 'anthropic.claude-sonnet-4-6' | 'amazon.nova-pro-v1:0'

    -- ── Feedback del usuario ──────────────────────────────────
    feedback            JSONB,
    -- {
    --   rating:          4,              -- 1-5
    --   comment:         "Faltó citar el caso X",
    --   useful_chunks:   ["uuid1", "uuid2"],
    --   missing_info:    "No incluyó jurisprudencia española"
    -- }

    -- ── Memorias generadas en esta sesión ─────────────────────
    memory_ids          UUID[],
    -- UUIDs de entradas en user_memory creadas a partir de esta sesión

    -- ── Métricas ──────────────────────────────────────────────
    total_tokens        INT,            -- para control de coste (input + output)
    latency_ms          INT,            -- tiempo total de respuesta en ms

    started_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ
);

CREATE INDEX        idx_sessions_user       ON research_sessions (user_id, started_at DESC);
CREATE INDEX        idx_sessions_recent     ON research_sessions (started_at DESC);
CREATE VECTOR INDEX idx_sessions_query      ON research_sessions (query_embedding) WHERE query_embedding IS NOT NULL;
