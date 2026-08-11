-- =============================================================
-- JurisMind — Schema para nuevas fuentes de datos
-- 2026-08-11 (v2: post-investigacion buenas practicas)
--
-- Diseno basado en:
--   1. Canonical Data Model — una tabla canonica (documents) para
--      todas las decisiones/sentencias, independientemente de la fuente
--   2. Medallion Architecture — tablas staging (bronze) para fuentes
--      que requieren pipeline de procesamiento (AEPD PDFs)
--   3. Entity Resolution — source_metadata JSONB para campos
--      especificos de cada fuente sin crear columnas huerfanas
--   4. Separation of Concerns — tablas separadas SOLO para entidades
--      de naturaleza distinta (quejas vs decisiones)
--
-- Cambios respecto a v1:
--   - ELIMINADA: cjeu_decisions (DPcuria → documents directamente)
--   - NUEVA: documents.source_metadata JSONB para campos por fuente
--   - RENOMBRADA: aepd_resolutions → aepd_pipeline (staging table)
--   - MANTENIDA: noyb_complaints (entidad distinta: queja, no decision)
--   - MANTENIDA: data_sources_sync (control operacional)
--
-- Filosofia:
--   - documents = capa Silver/canonica (datos limpios, unificados)
--   - aepd_pipeline = capa Bronze (raw, en proceso de extraccion)
--   - noyb_complaints = entidad distinta (no cabe en documents)
--   - Si la entidad ES una decision/sentencia → documents
--   - Si la entidad NO es una decision → tabla propia
-- =============================================================


-- =============================================================
-- STEP 1: Ampliar documents para soportar nuevas fuentes
--
-- Principio: "typed columns for core fields, JSONB for variable"
-- (PostgreSQL best practice 2026: JSONB es 160x mas lento que
-- columnas en benchmarks, pero perfecto para attrs variables)
-- =============================================================

-- Nuevos valores de source: 'dpcuria' | 'aepd'
-- (ya existen: 'gdprhub' | 'eurlex' | 'enforcement_tracker')

-- source_metadata: campos especificos de cada fuente que no
-- encajan en el schema canonico. Evita "column bloat".
--
-- Ejemplos por fuente:
--   dpcuria: {
--     "procedural_type": "preliminary_ruling",
--     "legal_category": "general_data_protection",
--     "referral_date": "2018-05-25",
--     "key_points": ["Right to erasure has limits"],
--     "dpcuria_url": "https://dpcuria.eu/...",
--     "curia_url": "https://curia.europa.eu/..."
--   }
--   aepd: {
--     "expediente": "PS-00148-2025",
--     "procedure_type": "PS",
--     "concepts": ["videovigilancia", "telecomunicaciones"],
--     "extraction_quality": 0.95,
--     "pdf_url": "https://www.aepd.es/documento/ps-00148-2025.pdf"
--   }
--   enforcement_tracker: {
--     "tracker_id": "1234",
--     "quota": "y",
--     "etid": "..."
--   }
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_metadata JSONB;

-- Indice GIN para consultas sobre source_metadata
-- Permite: WHERE source_metadata->>'procedural_type' = 'preliminary_ruling'
CREATE INDEX IF NOT EXISTS idx_doc_source_metadata
    ON documents USING GIN (source_metadata) WHERE source_metadata IS NOT NULL;

-- Indice parcial para encontrar documentos DPcuria rapidamente
CREATE INDEX IF NOT EXISTS idx_doc_dpcuria
    ON documents (decision_date DESC) WHERE source = 'dpcuria';

-- Indice parcial para encontrar documentos AEPD rapidamente
CREATE INDEX IF NOT EXISTS idx_doc_aepd
    ON documents (decision_date DESC) WHERE source = 'aepd';


-- =============================================================
-- TABLE: noyb_complaints
-- Quejas estrategicas de noyb.eu (~900 casos)
--
-- POR QUE tabla separada (no documents):
--   - Son QUEJAS, no decisiones — distinta naturaleza juridica
--   - Tienen ciclo de vida propio: pending → won/lost (meses/anos)
--   - Una queja puede resultar en 0, 1 o N decisiones de DPAs
--   - La relacion queja→decision es 1:N (via document_id nullable)
--   - Se actualizan frecuentemente (el status cambia con el tiempo)
--
-- Regla del Canonical Data Model: "tabla separada SOLO para
-- entidades de naturaleza distinta". Una queja NO es una decision.
-- =============================================================

CREATE TABLE IF NOT EXISTS noyb_complaints (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identificador unico de noyb
    case_id             TEXT            NOT NULL UNIQUE,
    -- Formato: "C001", "C002", ... "C900+"

    -- Partes
    controller          TEXT,
    -- Empresa o entidad demandada ("Google LLC", "Meta Platforms")

    -- DPA asignada
    dpa_name            TEXT,
    -- Autoridad que tramita la queja ("CNIL", "DPC", "AEPD")
    dpa_country         TEXT,
    -- Pais de la DPA ("France", "Ireland", "Spain")

    -- Estado y resultado
    status              TEXT            NOT NULL,
    -- 'Won' | 'Partly Won' | 'Pending' | 'Lost' | 'Other Outcome'

    duration_text       TEXT,
    -- Texto original de duracion ("2 years, 3 months", "pending")
    duration_days       INT,
    -- Duracion en dias (calculado si posible, NULL si pending)

    -- Detalle
    detail_url          TEXT,
    -- URL a la pagina de detalle en noyb.eu
    summary             TEXT,
    -- Resumen si disponible en la pagina de detalle

    -- Relacion con documents (cuando la queja resulto en decision)
    document_id         UUID            REFERENCES documents (id) ON DELETE SET NULL,
    -- FK a documents si existe una decision de DPA que corresponde
    -- Permite: "esta queja de noyb resulto en ESTA decision de la CNIL"

    -- Control
    scraped_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_noyb_status
    ON noyb_complaints (status);
CREATE INDEX IF NOT EXISTS idx_noyb_dpa
    ON noyb_complaints (dpa_country);
CREATE INDEX IF NOT EXISTS idx_noyb_doc
    ON noyb_complaints (document_id) WHERE document_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_noyb_pending
    ON noyb_complaints (scraped_at) WHERE status = 'Pending';
-- Indice de pending: encontrar rapidamente quejas sin resolver
-- para la feature "what's still cooking at your DPA"


-- =============================================================
-- TABLE: aepd_pipeline
-- Tabla STAGING (capa Bronze del Medallion Architecture)
-- para resoluciones sancionadoras de la AEPD
--
-- Flujo completo:
--   1. discovered  → URL probada, PDF existe (1,388 descubiertos)
--   2. downloaded  → PDF descargado al filesystem local
--   3. extracted   → texto extraido con PyMuPDF
--   4. parsed      → metadatos extraidos del texto (LLM o regex)
--   5. promoted    → fila creada en documents (source='aepd')
--
-- Cuando un registro llega a "promoted", se crea su fila en
-- la tabla documents con los metadatos extraidos, y se guarda
-- el document_id aqui para trazabilidad. La fila en aepd_pipeline
-- NO se borra — queda como registro de lineage/auditoria.
--
-- Esto es el patron Bronze→Silver del Medallion Architecture:
--   Bronze = aepd_pipeline (raw, con errores de OCR, incomplete)
--   Silver = documents (limpio, verificado, schema canonico)
-- =============================================================

CREATE TABLE IF NOT EXISTS aepd_pipeline (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identificador unico
    expediente          TEXT            NOT NULL UNIQUE,
    -- Formato: "PS-00148-2025", "PS-00001-2024"

    procedure_type      TEXT            NOT NULL DEFAULT 'PS',
    -- 'PS' (Procedimiento Sancionador) — unico tipo por ahora

    year                SMALLINT        NOT NULL,
    number              SMALLINT        NOT NULL,

    -- PDF source
    pdf_url             TEXT            NOT NULL,
    pdf_size_bytes      INT,
    pdf_downloaded      BOOLEAN         NOT NULL DEFAULT FALSE,

    -- Texto extraido del PDF (raw, sin limpiar)
    raw_text            TEXT,
    text_language       TEXT            DEFAULT 'es',
    text_extracted_at   TIMESTAMPTZ,
    extraction_quality  FLOAT,
    -- 0.0-1.0: ratio de caracteres validos vs total

    -- Metadatos extraidos (por LLM o regex del texto)
    -- Estos campos se copian a documents cuando se promueve
    controller_name     TEXT,
    fine_amount         BIGINT,
    gdpr_articles       TEXT[],
    sector              TEXT,
    resolution_date     DATE,
    concepts            TEXT[],

    -- Estado del pipeline
    pipeline_status     TEXT            NOT NULL DEFAULT 'discovered',
    -- 'discovered' | 'downloaded' | 'extracted' | 'parsed' | 'promoted'

    -- Relacion con documents (cuando se promueve a Silver)
    document_id         UUID            REFERENCES documents (id) ON DELETE SET NULL,

    -- Control y lineage
    discovered_at       TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_aepd_year
    ON aepd_pipeline (year DESC);
CREATE INDEX IF NOT EXISTS idx_aepd_status
    ON aepd_pipeline (pipeline_status);
CREATE INDEX IF NOT EXISTS idx_aepd_fine
    ON aepd_pipeline (fine_amount DESC) WHERE fine_amount IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_aepd_pending
    ON aepd_pipeline (discovered_at)
    WHERE pipeline_status IN ('discovered', 'downloaded');
-- Indice para el pipeline: encontrar rapidamente lo que falta


-- =============================================================
-- TABLE: data_sources_sync
-- Control de sincronizacion por fuente de datos
--
-- Permite:
--   - Saber cuando fue la ultima sincronizacion de cada fuente
--   - Controlar la frecuencia de actualizacion
--   - Registrar errores de sincronizacion
--   - "Updates since last visit" se basa en esto
-- =============================================================

CREATE TABLE IF NOT EXISTS data_sources_sync (
    source_name         TEXT            PRIMARY KEY,
    -- 'tracker' | 'gdprhub' | 'noyb' | 'dpcuria' | 'aepd_rss' | 'aepd_pdf'

    last_sync_at        TIMESTAMPTZ,
    last_sync_status    TEXT            DEFAULT 'never',
    -- 'success' | 'partial' | 'error' | 'never'

    records_total       INT             DEFAULT 0,
    records_new         INT             DEFAULT 0,

    last_error          TEXT,

    sync_config         JSONB,
    -- Configuracion especifica por fuente:
    -- noyb: {max_pages: 45}
    -- aepd_rss: {feed_url: "https://..."}
    -- aepd_pdf: {years: [2022,2023,2024,2025,2026], max_number: 600}

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- Seed con las fuentes conocidas
INSERT INTO data_sources_sync (source_name, last_sync_status) VALUES
    ('tracker', 'success'),
    ('gdprhub', 'success'),
    ('noyb', 'never'),
    ('dpcuria', 'never'),
    ('aepd_rss', 'never'),
    ('aepd_pdf', 'never')
ON CONFLICT (source_name) DO NOTHING;
