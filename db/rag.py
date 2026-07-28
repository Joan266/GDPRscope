"""
JurisMind — Motor RAG híbrido

Flujo para query(text, user_id):
  1. embed_query()            → vector [1024 floats] via Bedrock Titan V2
  2. search_vector_chunks()   → top-K child chunk_ids por cosine distance (C-SPANN)
  2. search_text_chunks()     → top-K child chunk_ids por ts_rank (tsvector BM25)
  3. reciprocal_rank_fusion() → merged & re-ranked por RRF
  4. fetch_parent_context()   → parent content + doc metadata por cada hit
  5. fetch_user_memory()      → top-5 memorias relevantes del usuario (ANN)
  6. build_prompt()           → system + user prompt con contexto y citas
  7. call_llm()               → Claude Sonnet via Bedrock → respuesta citada
  8. save_session()           → INSERT research_sessions → devuelve QueryResult

Uso:
    DATABASE_URL=... python db/rag.py --query "¿Qué dice la jurisprudencia sobre el derecho al olvido?" --user-id test
    DATABASE_URL=... python db/rag.py --query "legitimate interest marketing" --user-id test --jurisdiction France
    DATABASE_URL=... python db/rag.py --query "consent cookies" --user-id test --no-llm
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass

import boto3
import psycopg

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
AWS_REGION        = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID_EMBED    = "amazon.titan-embed-text-v2:0"
MODEL_ID_LLM      = "anthropic.claude-sonnet-4-6"
EMBED_DIMS        = 1024
K_VECTOR          = 20    # chunks por rama de búsqueda
K_TEXT            = 20
K_MEMORY          = 5     # memorias de usuario a recuperar
RRF_K             = 60    # constante RRF estándar
TOP_N_CONTEXT     = 8     # chunks padre enviados al LLM tras RRF
MAX_CHARS_QUERY   = 30_000

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    session_id: str
    response:   str
    citations:  list[dict]
    latency_ms: int


# ── Bedrock ────────────────────────────────────────────────────────────────────

def make_bedrock_client() -> boto3.client:
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def embed_query(client, text: str) -> list[float]:
    """Embed via Titan V2. Sin retry — consulta interactiva, falla rápido."""
    text = text[:MAX_CHARS_QUERY].strip()
    if not text:
        raise ValueError("Query vacía")

    body = json.dumps({
        "inputText": text,
        "dimensions": EMBED_DIMS,
        "normalize":  True,
    })
    resp = client.invoke_model(
        modelId=MODEL_ID_EMBED,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def vector_to_pg(embedding: list[float]) -> str:
    """Serializa vector a literal VECTOR de CockroachDB/PostgreSQL."""
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


# ── Filter builder ─────────────────────────────────────────────────────────────

def _build_filter_clause(filters: dict) -> tuple[str, list]:
    """Construye cláusulas AND extra para los filtros opcionales."""
    clauses: list[str] = []
    params:  list      = []

    if filters.get("jurisdiction"):
        clauses.append("AND d.jurisdiction = %s")
        params.append(filters["jurisdiction"])

    if filters.get("source"):
        clauses.append("AND d.source = %s")
        params.append(filters["source"])

    if filters.get("gdpr_article"):
        clauses.append("AND %s = ANY(d.gdpr_articles)")
        params.append(filters["gdpr_article"])

    return "\n  ".join(clauses), params


# ── Search ─────────────────────────────────────────────────────────────────────

_SQL_VECTOR = """
SELECT c.id
FROM   chunks c
JOIN   documents d ON d.id = c.document_id
WHERE  c.chunk_type = 'child'
  AND  c.embedding  IS NOT NULL
  {filter_clause}
ORDER BY c.embedding <=> %s::VECTOR({dims})
LIMIT  %s
"""

_SQL_TEXT = """
SELECT c.id
FROM   chunks c
JOIN   documents d ON d.id = c.document_id
WHERE  c.chunk_type = 'child'
  AND  c.search_vector @@ plainto_tsquery('english', %s)
  {filter_clause}
ORDER BY ts_rank(c.search_vector, plainto_tsquery('english', %s)) DESC
LIMIT  %s
"""


def search_vector_chunks(
    cur: psycopg.Cursor, query_vec: list[float], k: int, filters: dict
) -> list[str]:
    clause, params = _build_filter_clause(filters)
    sql = _SQL_VECTOR.format(filter_clause=clause, dims=EMBED_DIMS)
    cur.execute(sql, params + [vector_to_pg(query_vec), k])
    return [str(row[0]) for row in cur.fetchall()]


def search_text_chunks(
    cur: psycopg.Cursor, query_text: str, k: int, filters: dict
) -> list[str]:
    clause, params = _build_filter_clause(filters)
    sql = _SQL_TEXT.format(filter_clause=clause)
    cur.execute(sql, [query_text] + params + [query_text, k])
    return [str(row[0]) for row in cur.fetchall()]


# ── RRF ───────────────────────────────────────────────────────────────────────

def reciprocal_rank_fusion(
    vector_hits: list[str],
    text_hits:   list[str],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for rank, cid in enumerate(vector_hits):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    for rank, cid in enumerate(text_hits):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ── Context fetching ───────────────────────────────────────────────────────────

_SQL_PARENT_CONTEXT = """
SELECT c.id  AS child_id,
       p.id  AS parent_id, p.content, p.section,
       d.id  AS doc_id, d.title, d.authority, d.authority_abbrev,
       d.jurisdiction, d.decision_date, d.decision_year,
       d.fine_amount, d.fine_currency, d.gdpr_articles,
       d.source_urls, d.source, d.case_number, d.ecli, d.celex
FROM   chunks c
JOIN   chunks p ON p.id = c.parent_id
JOIN   documents d ON d.id = p.document_id
WHERE  c.id = ANY(%s)
"""


def fetch_parent_context(
    cur: psycopg.Cursor,
    child_chunk_ids: list[str],
    rrf_scores: dict[str, float],
    top_n: int,
) -> list[dict]:
    """
    Busca parent chunks para los child hits dados.
    Deduplica: si dos child chunks comparten parent, conserva el de mayor score RRF.
    Devuelve lista re-ordenada por score, limitada a top_n.
    """
    if not child_chunk_ids:
        return []

    cur.execute(_SQL_PARENT_CONTEXT, (child_chunk_ids,))
    rows = cur.fetchall()

    # Deduplicar por parent_id manteniendo el child con mayor score
    best_row_by_parent:   dict[str, tuple] = {}
    best_score_by_parent: dict[str, float] = {}

    for row in rows:
        child_id  = str(row[0])
        parent_id = str(row[1])
        score = rrf_scores.get(child_id, 0.0)
        if parent_id not in best_score_by_parent or score > best_score_by_parent[parent_id]:
            best_row_by_parent[parent_id]   = row
            best_score_by_parent[parent_id] = score

    sorted_rows = sorted(
        best_row_by_parent.values(),
        key=lambda r: best_score_by_parent.get(str(r[1]), 0.0),
        reverse=True,
    )[:top_n]

    results = []
    for row in sorted_rows:
        (child_id, parent_id, content, section,
         doc_id, title, authority, authority_abbrev,
         jurisdiction, decision_date, decision_year,
         fine_amount, fine_currency, gdpr_articles,
         source_urls, source, case_number, ecli, celex) = row

        results.append({
            "child_id":         str(child_id),
            "parent_id":        str(parent_id),
            "content":          content,
            "section":          section,
            "doc_id":           str(doc_id),
            "title":            title or "",
            "authority":        authority,
            "authority_abbrev": authority_abbrev,
            "jurisdiction":     jurisdiction,
            "decision_date":    str(decision_date) if decision_date else None,
            "decision_year":    decision_year,
            "fine_amount":      fine_amount,
            "fine_currency":    fine_currency,
            "gdpr_articles":    gdpr_articles or [],
            "source_urls":      source_urls,      # JSONB → auto-deserialized by psycopg
            "source":           source,
            "case_number":      case_number,
            "ecli":             ecli,
            "celex":            celex,
        })

    return results


# ── User memory ────────────────────────────────────────────────────────────────

_SQL_USER_MEMORY = f"""
SELECT content, memory_type, context, importance_score
FROM   user_memory
WHERE  user_id = %s
  AND  embedding IS NOT NULL
  AND  (expires_at IS NULL OR expires_at > now())
ORDER BY embedding <=> %s::VECTOR({EMBED_DIMS})
LIMIT  %s
"""


def fetch_user_memory(
    cur: psycopg.Cursor,
    user_id: str,
    query_vec: list[float],
    limit: int = K_MEMORY,
) -> list[dict]:
    try:
        cur.execute(_SQL_USER_MEMORY, (user_id, vector_to_pg(query_vec), limit))
    except Exception:
        return []

    return [
        {
            "content":          row[0],
            "memory_type":      row[1],
            "context":          row[2],
            "importance_score": row[3],
        }
        for row in cur.fetchall()
    ]


# ── Prompt ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are JurisMind, an expert GDPR legal research assistant helping data protection \
officers and privacy lawyers. You provide precise, sourced analysis of GDPR \
jurisprudence from DPA decisions, national courts, and the CJEU.

Rules:
- Always cite sources as [Authority — Case Title — Year]
- Distinguish DPA decisions (administrative) from court judgments (judicial)
- Note jurisdiction divergences across EU member states
- If retrieved context is insufficient, say so explicitly
- Respond in the same language the user writes in"""


def build_prompt(
    query: str,
    contexts: list[dict],
    memories: list[dict],
) -> tuple[str, str]:
    context_lines: list[str] = []
    for i, ctx in enumerate(contexts, start=1):
        authority_label = ctx.get("authority_abbrev") or ctx.get("authority") or "Unknown"
        fine_str    = (
            f"Fine: {ctx['fine_currency']} {ctx['fine_amount']:,}"
            if ctx.get("fine_amount") else ""
        )
        articles    = ", ".join(ctx["gdpr_articles"]) if ctx.get("gdpr_articles") else ""
        articles_str = f"Articles: {articles}" if articles else ""
        meta = "  ".join(filter(None, [fine_str, articles_str]))

        context_lines.append(
            f"### [{i}] {ctx['title']} | {authority_label} | "
            f"{ctx.get('jurisdiction', '')} | {ctx.get('decision_year', '')}"
        )
        if meta:
            context_lines.append(meta)
        context_lines.append(ctx["content"] or "")
        context_lines.append("")

    context_block = "\n".join(context_lines).strip()

    memory_block = ""
    if memories:
        lines = ["## Your Previous Research Context"]
        lines.extend(m["content"] for m in memories)
        memory_block = "\n".join(lines) + "\n\n"

    user_prompt = (
        f"{memory_block}"
        f"## Retrieved GDPR Jurisprudence\n\n"
        f"{context_block}\n\n"
        f"## Question\n\n"
        f"{query}"
    )

    return _SYSTEM_PROMPT, user_prompt


# ── LLM call ──────────────────────────────────────────────────────────────────

def call_llm(client, system_prompt: str, user_prompt: str) -> str:
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    })
    resp = client.invoke_model(
        modelId=MODEL_ID_LLM,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["content"][0]["text"]


# ── Session saving ─────────────────────────────────────────────────────────────

_SQL_INSERT_SESSION = f"""
INSERT INTO research_sessions (
    user_id, query, query_embedding, retrieved_chunks,
    response, response_model, latency_ms, ended_at
) VALUES (
    %s, %s, %s::VECTOR({EMBED_DIMS}), %s,
    %s, %s, %s, now()
)
RETURNING id
"""


def save_session(
    conn: psycopg.Connection,
    user_id: str,
    query: str,
    query_vec: list[float],
    contexts: list[dict],
    rrf_scores: dict[str, float],
    response: str,
    model: str,
    latency_ms: int,
) -> str:
    retrieved_chunks = [
        {
            "chunk_id":          ctx["parent_id"],
            "document_id":       ctx["doc_id"],
            "document_title":    ctx["title"],
            "relevance_score":   rrf_scores.get(ctx["child_id"], 0.0),
            "cited_in_response": True,
        }
        for ctx in contexts
    ]

    with conn.cursor() as cur:
        cur.execute(
            _SQL_INSERT_SESSION,
            (
                user_id,
                query,
                vector_to_pg(query_vec),
                json.dumps(retrieved_chunks),
                response,
                model,
                latency_ms,
            ),
        )
        session_id = str(cur.fetchone()[0])
        conn.commit()

    return session_id


# ── Orchestrator ───────────────────────────────────────────────────────────────

def query(
    conn: psycopg.Connection,
    bedrock_client,
    user_id: str,
    query_text: str,
    filters: dict | None = None,
    top_n: int = TOP_N_CONTEXT,
    no_llm: bool = False,
) -> QueryResult:
    filters = filters or {}
    t_start = time.monotonic()

    # 1. Embed query
    log.info("Embedding query...")
    query_vec = embed_query(bedrock_client, query_text)

    with conn.cursor() as cur:
        # 2. Hybrid search
        log.info("Vector search (C-SPANN)...")
        vector_hits = search_vector_chunks(cur, query_vec, K_VECTOR, filters)
        log.info("  → %d vector hits", len(vector_hits))

        log.info("Text search (tsvector)...")
        text_hits = search_text_chunks(cur, query_text, K_TEXT, filters)
        log.info("  → %d text hits", len(text_hits))

        # 3. RRF
        rrf_ranked    = reciprocal_rank_fusion(vector_hits, text_hits)
        rrf_scores    = dict(rrf_ranked)
        # Fetch top_n*3 child IDs to allow dedup by parent and still land top_n parents
        top_child_ids = [cid for cid, _ in rrf_ranked[: top_n * 3]]
        log.info("RRF: %d unique chunks (using top %d as candidates)", len(rrf_ranked), len(top_child_ids))

        # 4. Parent context
        log.info("Fetching parent context...")
        contexts = fetch_parent_context(cur, top_child_ids, rrf_scores, top_n)
        log.info("  → %d parent contexts", len(contexts))

        # 5. User memory
        log.info("Fetching user memory (user=%s)...", user_id)
        memories = fetch_user_memory(cur, user_id, query_vec)
        log.info("  → %d memories", len(memories))

    if no_llm:
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _print_contexts(contexts, rrf_scores)
        return QueryResult(
            session_id="dry-run",
            response="[--no-llm: LLM call skipped]",
            citations=_build_citations(contexts),
            latency_ms=latency_ms,
        )

    # 6. Build prompt
    system_prompt, user_prompt = build_prompt(query_text, contexts, memories)

    # 7. LLM
    log.info("Calling %s via Bedrock...", MODEL_ID_LLM)
    response = call_llm(bedrock_client, system_prompt, user_prompt)

    latency_ms = int((time.monotonic() - t_start) * 1000)
    log.info("Response in %d ms", latency_ms)

    # 8. Save session
    session_id = save_session(
        conn, user_id, query_text, query_vec,
        contexts, rrf_scores, response, MODEL_ID_LLM, latency_ms,
    )

    return QueryResult(
        session_id=session_id,
        response=response,
        citations=_build_citations(contexts),
        latency_ms=latency_ms,
    )


def _build_citations(contexts: list[dict]) -> list[dict]:
    citations = []
    for ctx in contexts:
        source_url = None
        urls = ctx.get("source_urls")
        if isinstance(urls, list) and urls:
            source_url = urls[0].get("url")
        citations.append({
            "title":      ctx["title"],
            "authority":  ctx.get("authority_abbrev") or ctx.get("authority", ""),
            "year":       ctx.get("decision_year"),
            "source_url": source_url,
        })
    return citations


def _print_contexts(contexts: list[dict], rrf_scores: dict[str, float]) -> None:
    print(f"\n{'=' * 60}")
    print(f"Retrieved {len(contexts)} parent contexts (--no-llm mode):")
    print("=" * 60)
    for i, ctx in enumerate(contexts, start=1):
        score = rrf_scores.get(ctx["child_id"], 0.0)
        print(f"\n[{i}] {ctx['title']}")
        print(f"    Authority : {ctx.get('authority_abbrev') or ctx.get('authority') or 'N/A'}")
        print(f"    Jurisdiction: {ctx.get('jurisdiction') or 'N/A'} | Year: {ctx.get('decision_year') or 'N/A'}")
        print(f"    RRF score : {score:.4f}")
        print(f"    Section   : {ctx.get('section') or 'N/A'}")
        snippet = (ctx["content"] or "")[:300].replace("\n", " ")
        print(f"    Snippet   : {snippet}...")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Motor RAG híbrido")
    parser.add_argument("--query",        required=True,
                        help="Consulta en lenguaje natural")
    parser.add_argument("--user-id",      default="anonymous",
                        help="ID del usuario (default: anonymous)")
    parser.add_argument("--jurisdiction", default=None,
                        help="Filtrar por jurisdicción (e.g. France, Germany, EU)")
    parser.add_argument("--source",       default=None,
                        choices=["gdprhub", "enforcement_tracker", "eurlex"],
                        help="Filtrar por fuente de datos")
    parser.add_argument("--no-llm",       action="store_true",
                        help="Solo búsqueda — muestra chunks recuperados sin llamar al LLM")
    parser.add_argument("--top-n",        type=int, default=TOP_N_CONTEXT,
                        help=f"Chunks padre enviados al LLM (default: {TOP_N_CONTEXT})")
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        log.error("  export DATABASE_URL='postgresql://user:pass@host:26257/jurismind?sslmode=verify-full'")
        sys.exit(1)

    log.info("Conectando a CockroachDB...")
    conn = psycopg.connect(DATABASE_URL)

    log.info("Iniciando Bedrock client (region=%s)...", AWS_REGION)
    client = make_bedrock_client()

    filters: dict = {}
    if args.jurisdiction:
        filters["jurisdiction"] = args.jurisdiction
    if args.source:
        filters["source"] = args.source

    result = query(
        conn=conn,
        bedrock_client=client,
        user_id=args.user_id,
        query_text=args.query,
        filters=filters,
        top_n=args.top_n,
        no_llm=args.no_llm,
    )

    conn.close()

    if not args.no_llm:
        print(f"\n{'=' * 60}")
        print(result.response)
        print(f"\n{'─' * 60}")
        print(f"Session ID : {result.session_id}")
        print(f"Latency    : {result.latency_ms} ms")
        print(f"\nCitations ({len(result.citations)}):")
        for c in result.citations:
            label = f"[{c['authority']} — {c['title']} — {c['year']}]"
            print(f"  {label}")
            if c.get("source_url"):
                print(f"    {c['source_url']}")
    else:
        print(f"\nLatency: {result.latency_ms} ms")


if __name__ == "__main__":
    main()
