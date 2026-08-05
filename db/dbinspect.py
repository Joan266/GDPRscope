"""
JurisMind — Inspector de base de datos

Inspecciona documentos y chunks en CockroachDB sin SQL manual.

Uso:
    python db/inspect.py stats
    python db/inspect.py list [--source gdprhub|eurlex|enforcement_tracker] [--limit 20] [--offset 0]
    python db/inspect.py show <source_id_o_uuid>
    python db/inspect.py chunks <doc_uuid>
"""

import argparse
import os
import sys
from textwrap import shorten

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")


# ── DB ─────────────────────────────────────────────────────────────────────────

def get_conn() -> psycopg.Connection:
    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL no definida. Exporta la variable de entorno.")
    return psycopg.connect(DATABASE_URL)


# ── Helpers de display ─────────────────────────────────────────────────────────

def hr(char: str = "-", width: int = 80) -> None:
    print(char * width)


def fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def fmt_eur(amount: int | None) -> str:
    if amount is None:
        return ""
    if amount >= 1_000_000:
        return f"€{amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"€{amount/1_000:.0f}K"
    return f"€{amount}"


def trunc(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    return shorten(text, width=max_len, placeholder="…")


def print_table(headers: list[str], rows: list[list[str]], col_widths: list[int]) -> None:
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    hr()
    print(fmt.format(*[h[:w] for h, w in zip(headers, col_widths)]))
    hr()
    for row in rows:
        cells = [str(v)[:w] if v else "" for v, w in zip(row, col_widths)]
        print(fmt.format(*cells))
    hr()
    print(f"  {len(rows)} fila(s)")


# ── Comandos ───────────────────────────────────────────────────────────────────

def cmd_stats() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, document_type, COUNT(*) AS n
                FROM documents
                GROUP BY source, document_type
                ORDER BY source, document_type
            """)
            doc_rows = cur.fetchall()

            cur.execute("SELECT COUNT(*) FROM documents")
            total_docs = cur.fetchone()[0]

            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(embedding) AS with_embedding
                FROM chunks
            """)
            total_chunks, embedded = cur.fetchone()

            cur.execute("""
                SELECT COUNT(*) FROM chunks WHERE chunk_type = 'parent'
            """)
            parent_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM chunks WHERE chunk_type = 'child'
            """)
            child_count = cur.fetchone()[0]

    pending = total_chunks - embedded
    pct = (embedded / total_chunks * 100) if total_chunks else 0

    print()
    print("  JURISMIND — Estado de la base de datos")
    hr("=")

    print(f"  Documentos totales: {fmt_int(total_docs)}")
    print()
    print("  Por fuente y tipo:")
    for source, doc_type, n in doc_rows:
        print(f"    {source:<25} {doc_type:<20} {fmt_int(n):>6}")

    hr()
    print(f"  Chunks totales:   {fmt_int(total_chunks)}")
    print(f"    parent:         {fmt_int(parent_count)}")
    print(f"    child:          {fmt_int(child_count)}")
    print(f"  Con embedding:    {fmt_int(embedded)} ({pct:.1f}%)")
    print(f"  Pendientes:       {fmt_int(pending)}")
    hr("=")
    print()


def cmd_list(source: str | None, limit: int, offset: int) -> None:
    sql = """
        SELECT
            id,
            source,
            title,
            jurisdiction,
            authority_abbrev,
            decision_year,
            fine_amount,
            array_to_string(gdpr_articles, ', ') AS articles
        FROM documents
        {where}
        ORDER BY decision_year DESC NULLS LAST, ingested_at DESC
        LIMIT %s OFFSET %s
    """
    where = "WHERE source = %s" if source else ""
    sql = sql.format(where=where)
    params = ([source, limit, offset] if source else [limit, offset])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    if not rows:
        print("Sin resultados.")
        return

    display_rows = []
    for doc_id, src, title, juris, auth, year, fine, articles in rows:
        display_rows.append([
            str(doc_id)[:8],
            src or "",
            trunc(title, 55),
            juris or "",
            auth or "",
            str(year) if year else "",
            fmt_eur(fine),
            trunc(articles, 30),
        ])

    print()
    print_table(
        headers=["ID",     "SOURCE",     "TITLE",                         "JURIS",    "AUTH",    "YEAR",  "FINE",    "ARTICLES"],
        rows=display_rows,
        col_widths=[8,      20,           55,                              12,         12,        4,       8,         30],
    )
    print()


def cmd_show(identifier: str) -> None:
    # Detecta si parece un UUID completo
    is_uuid = len(identifier) == 36 and identifier.count("-") == 4

    with get_conn() as conn:
        with conn.cursor() as cur:
            if is_uuid:
                cur.execute("SELECT * FROM documents WHERE id = %s", [identifier])
            else:
                cur.execute(
                    "SELECT * FROM documents WHERE source_id = %s OR source_id ILIKE %s",
                    [identifier, f"%{identifier}%"],
                )
            row = cur.fetchone()
            if not row:
                print(f"No encontrado: {identifier!r}")
                return
            col_names = [desc.name for desc in cur.description]
            doc = dict(zip(col_names, row))
            doc_id = doc["id"]

            cur.execute("""
                SELECT id, chunk_type, parent_id, chunk_index, section,
                       content_tokens, embedding IS NOT NULL AS has_embedding,
                       LEFT(content, 200) AS preview
                FROM chunks
                WHERE document_id = %s
                ORDER BY chunk_index
            """, [doc_id])
            chunks = cur.fetchall()

    print()
    hr("=")
    print(f"  {doc['title']}")
    hr("=")

    # Campos principales
    fields = [
        ("id",              str(doc["id"])),
        ("source",          doc["source"]),
        ("source_id",       doc["source_id"]),
        ("document_type",   doc["document_type"]),
        ("jurisdiction",    doc["jurisdiction"]),
        ("authority",       doc["authority"]),
        ("authority_abbrev",doc["authority_abbrev"]),
        ("case_number",     doc["case_number"]),
        ("ecli",            doc["ecli"]),
        ("celex",           doc["celex"]),
        ("decision_date",   str(doc["decision_date"]) if doc["decision_date"] else None),
        ("decision_year",   str(doc["decision_year"]) if doc["decision_year"] else None),
        ("case_type",       doc["case_type"]),
        ("outcome",         doc["outcome"]),
        ("fine_amount",     fmt_eur(doc["fine_amount"]) or None),
        ("sector",          doc["sector"]),
        ("controller_name", doc["controller_name"]),
        ("gdpr_articles",   str(doc["gdpr_articles"]) if doc["gdpr_articles"] else None),
        ("pipeline_version",doc["pipeline_version"]),
        ("ingested_at",     str(doc["ingested_at"])),
    ]
    for label, value in fields:
        if value:
            print(f"  {label:<20} {value}")

    # Textos semánticos
    for field in ("summary_teaser", "summary_facts", "summary_dispute", "summary_holding"):
        text = doc.get(field)
        if text:
            hr()
            print(f"  [{field}]")
            print(f"  {trunc(text, 300)}")

    # Chunks
    hr()
    print(f"  CHUNKS ({len(chunks)})")
    hr()
    for chunk_id, ctype, parent_id, idx, section, tokens, has_emb, preview in chunks:
        emb_tag = "[EMB]" if has_emb else "     "
        parent_tag = f" <- {str(parent_id)[:8]}" if parent_id else ""
        print(f"  [{idx:>3}] {ctype:<6} {emb_tag} {section or '':<12} {fmt_int(tokens):>5} tok  {str(chunk_id)[:8]}{parent_tag}")
        print(f"       {trunc(preview, 120)}")
    hr("=")
    print()


def cmd_chunks(doc_uuid: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.title, d.source
                FROM documents d
                WHERE d.id = %s
            """, [doc_uuid])
            doc_row = cur.fetchone()
            if not doc_row:
                print(f"Documento no encontrado: {doc_uuid!r}")
                return
            doc_title, doc_source = doc_row

            cur.execute("""
                SELECT id, chunk_type, parent_id, chunk_index, section,
                       content_tokens, embedding IS NOT NULL AS has_embedding,
                       LEFT(content, 200) AS preview
                FROM chunks
                WHERE document_id = %s
                ORDER BY chunk_index
            """, [doc_uuid])
            chunks = cur.fetchall()

    print()
    hr("=")
    print(f"  CHUNKS — {doc_title} [{doc_source}]")
    hr("=")

    if not chunks:
        print("  Sin chunks.")
    for chunk_id, ctype, parent_id, idx, section, tokens, has_emb, preview in chunks:
        emb_tag = "[EMB]" if has_emb else "[ - ]"
        parent_tag = f" <- {str(parent_id)[:8]}" if parent_id else "            "
        print(f"  [{idx:>3}] {ctype:<6} {emb_tag} {section or '':<12} {fmt_int(tokens):>5} tok  {str(chunk_id)[:8]}{parent_tag}")
        print(f"       {trunc(preview, 120)}")
        print()

    hr("=")
    print(f"  Total: {len(chunks)} chunk(s)")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="JurisMind — inspector de documentos y chunks en CockroachDB"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="Resumen: docs por fuente, chunks, embeddings")

    p_list = sub.add_parser("list", help="Tabla de documentos")
    p_list.add_argument("--source", choices=["gdprhub", "eurlex", "enforcement_tracker"])
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)

    p_show = sub.add_parser("show", help="Doc completo + sus chunks")
    p_show.add_argument("identifier", help="UUID o source_id (parcial)")

    p_chunks = sub.add_parser("chunks", help="Solo los chunks de un documento")
    p_chunks.add_argument("doc_uuid", help="UUID del documento")

    args = parser.parse_args()

    if args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "list":
        cmd_list(args.source, args.limit, args.offset)
    elif args.cmd == "show":
        cmd_show(args.identifier)
    elif args.cmd == "chunks":
        cmd_chunks(args.doc_uuid)


if __name__ == "__main__":
    main()
