-- Migration 002: document_links
-- Cross-reference table linking the same case across sources.
-- Replaces the directional canonical_id field on documents.

CREATE TABLE IF NOT EXISTS document_links (
    doc_a       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_b       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    link_type   TEXT NOT NULL DEFAULT 'same_case',
    confidence  REAL NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (doc_a, doc_b),
    CHECK (doc_a < doc_b),
    CHECK (confidence BETWEEN 0.0 AND 1.0),
    CHECK (link_type IN ('same_case', 'appeal', 'related'))
);

CREATE INDEX IF NOT EXISTS idx_doc_links_a ON document_links(doc_a);
CREATE INDEX IF NOT EXISTS idx_doc_links_b ON document_links(doc_b);
CREATE INDEX IF NOT EXISTS idx_doc_links_type ON document_links(link_type);
