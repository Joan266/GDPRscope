"""
JurisMind — Text chunker (parent-child pattern)

Patrón:
  - chunk 'parent': sección completa (500-3000 chars) → enviado al LLM como contexto
  - chunk 'child':  ventana deslizante de ~600 chars con 20% overlap → usado para embedding/retrieval

Los UUIDs se generan en Python para poder insertar parents e hijos en el mismo batch.
"""

import uuid

CHILD_SIZE    = 600   # chars ≈ 150 tokens
CHILD_OVERLAP = 120   # 20% overlap


def _new_id() -> str:
    return str(uuid.uuid4())


def _windows(text: str) -> list[str]:
    """Sliding windows sobre texto. Devuelve [text] si cabe en un solo chunk."""
    if len(text) <= CHILD_SIZE:
        return [text]
    result, start = [], 0
    while start < len(text):
        end = min(start + CHILD_SIZE, len(text))
        result.append(text[start:end])
        if end == len(text):
            break
        start += CHILD_SIZE - CHILD_OVERLAP
    return result


def _parents_for_doc(doc: dict) -> list[tuple[str, str]]:
    """Devuelve lista de (section_name, content) para un documento normalizado."""
    source = doc["source"]

    if source == "gdprhub":
        facts_text = "\n\n".join(filter(None, [
            doc.get("summary_teaser"),
            doc.get("summary_facts"),
        ])).strip()
        sections = [
            ("facts",   facts_text),
            ("holding", (doc.get("summary_holding") or "").strip()),
            ("dispute", (doc.get("summary_dispute") or "").strip()),
        ]
        return [(name, text) for name, text in sections if len(text) >= 80]

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
    Genera chunks parent + child para un documento.
    Devuelve lista de dicts listos para insertar en la tabla chunks.
    Los UUIDs están pre-generados — se pueden insertar en batch sin round-trips adicionales.
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

        # Siempre crear al menos un child: _windows() devuelve [text] si cabe en un chunk.
        # Sin children, el documento es invisible al RAG (busca chunk_type='child').
        for window in _windows(parent_text):
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
