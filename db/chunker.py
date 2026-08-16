"""
JurisMind — Text chunker (semantic sections)

Patron:
  - chunk 'parent': seccion completa → enviado al LLM como contexto
  - chunk 'child':  trozo para embedding/retrieval

Secciones GDPRhub:
  - holding_summary:  resumen editorial (campo summary_holding) → 1 child entero
  - holding_decision: texto completo de la decision (campo holding_full_text) → children de 2500 chars
  - facts:            teaser + facts → 1 child si <3000, sino split
  - dispute:          1 child entero
  - teaser:           enforcement_tracker/eurlex → 1 child entero

Los UUIDs se generan en Python para poder insertar parents e hijos en el mismo batch.
"""

import uuid

# Child sizes by section type
CHILD_SIZE_DEFAULT  = 2500   # chars ~625 tokens — for long decision text
CHILD_OVERLAP       = 500    # 20% overlap
CHILD_MAX_NOSPLIT   = 3000   # sections shorter than this → 1 child, no splitting


def _new_id() -> str:
    return str(uuid.uuid4())


def _windows(text: str, child_size: int = CHILD_SIZE_DEFAULT, overlap: int = CHILD_OVERLAP) -> list[str]:
    """Sliding windows over text. Returns [text] if it fits in a single chunk."""
    if len(text) <= child_size:
        return [text]
    result, start = [], 0
    while start < len(text):
        end = min(start + child_size, len(text))
        result.append(text[start:end])
        if end == len(text):
            break
        start += child_size - overlap
    return result


def _parents_for_doc(doc: dict) -> list[tuple[str, str]]:
    """Returns list of (section_name, content) for a normalized document."""
    source = doc["source"]

    if source == "gdprhub":
        facts_text = "\n\n".join(filter(None, [
            doc.get("summary_teaser"),
            doc.get("summary_facts"),
        ])).strip()

        sections: list[tuple[str, str]] = []

        if len(facts_text) >= 80:
            sections.append(("facts", facts_text))

        # holding_summary from summary_holding (editorial, already split in DB)
        holding_summary = (doc.get("summary_holding") or "").strip()
        if len(holding_summary) >= 80:
            sections.append(("holding_summary", holding_summary))

        # holding_decision from holding_full_text (raw decision, already split in DB)
        holding_decision = (doc.get("holding_full_text") or "").strip()
        if len(holding_decision) >= 80:
            sections.append(("holding_decision", holding_decision))

        dispute_text = (doc.get("summary_dispute") or "").strip()
        if len(dispute_text) >= 80:
            sections.append(("dispute", dispute_text))

        return sections

    if source == "enforcement_tracker":
        articles = ", ".join(doc.get("gdpr_articles") or [])
        lines = [
            doc.get("title", ""),
            f"Authority: {doc.get('authority', '')}",
            f"Jurisdiction: {doc.get('jurisdiction', '')}",
            f"Fine: EUR {doc['fine_amount']:,}" if doc.get("fine_amount") else "No monetary fine",
            f"Sector: {doc.get('sector', '')}",
            f"GDPR articles: {articles}",
            f"Violation: {doc.get('summary_teaser', '')}",
        ]
        text = "\n".join(l for l in lines if l.strip())
        return [("teaser", text)] if text else []

    if source == "eurlex":
        lines = filter(None, [
            doc.get("title"),
            f"CELEX: {doc.get('celex', '')}",
            f"ECLI: {doc.get('ecli', '')}",
            f"Date: {doc.get('decision_date', '')}",
            f"Jurisdiction: {doc.get('jurisdiction', '')}",
        ])
        text = "\n".join(lines)
        return [("teaser", text)] if text else []

    return []


def make_chunks(doc_id: str, doc: dict) -> list[dict]:
    """
    Generate parent + child chunks for a document.
    Returns list of dicts ready to insert into the chunks table.

    Chunking strategy per section:
      - holding_summary: 1 child (no split) — editorial summary, ~2100 chars
      - holding_decision: children of 2500 chars — full decision text, ~40K chars
      - facts: 1 child if <3000 chars, else split at 2500
      - dispute: 1 child (usually <500 chars)
      - teaser: 1 child (usually <400 chars)
    """
    chunks: list[dict] = []
    chunk_index = 0

    for section, parent_text in _parents_for_doc(doc):
        parent_id = _new_id()

        chunks.append({
            "id":            parent_id,
            "document_id":   doc_id,
            "chunk_type":    "parent",
            "parent_id":     None,
            "chunk_index":   chunk_index,
            "content":       parent_text,
            "content_tokens": len(parent_text) // 4,
            "section":       section,
        })
        chunk_index += 1

        # Decide child chunking strategy based on section
        if section in ("holding_summary", "dispute", "teaser"):
            # Short/high-value sections → 1 child, no splitting
            windows = [parent_text]
        elif len(parent_text) <= CHILD_MAX_NOSPLIT:
            # Short enough to keep as single child
            windows = [parent_text]
        else:
            # Long sections (holding_decision, long facts) → split
            windows = _windows(parent_text, CHILD_SIZE_DEFAULT, CHILD_OVERLAP)

        for window in windows:
            chunks.append({
                "id":            _new_id(),
                "document_id":   doc_id,
                "chunk_type":    "child",
                "parent_id":     parent_id,
                "chunk_index":   chunk_index,
                "content":       window,
                "content_tokens": len(window) // 4,
                "section":       section,
            })
            chunk_index += 1

    return chunks
