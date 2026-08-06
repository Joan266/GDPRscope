"""
JurisMind — Article summaries builder

Para cada artículo GDPR con suficientes casos en la DB, genera un resumen
narrativo de enforcement y lo añade a data/corpus_index.json bajo
la clave `article_summaries`.

Coste estimado: ~$0.30-0.50 para 15-20 artículos (Claude Haiku).
Idempotente: si el artículo ya tiene summary en el JSON, lo omite.

Uso:
    DATABASE_URL=... ANTHROPIC_API_KEY=... python db/build_article_summaries.py --dry-run
    DATABASE_URL=... ANTHROPIC_API_KEY=... python db/build_article_summaries.py
    DATABASE_URL=... ANTHROPIC_API_KEY=... python db/build_article_summaries.py --min-cases 3
    DATABASE_URL=... ANTHROPIC_API_KEY=... python db/build_article_summaries.py --articles "Art. 6,Art. 32"
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import anthropic
import psycopg

# ── Config ─────────────────────────────────────────────────────────────────────

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CORPUS_INDEX_PATH = Path(__file__).parent.parent / "data" / "corpus_index.json"

# Use Haiku for cost efficiency — summaries don't need heavy reasoning
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# Minimum cases to generate a summary (articles with fewer are too sparse)
MIN_CASES_DEFAULT = 5

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# ── Article normalisation ───────────────────────────────────────────────────────

# Pattern to extract canonical article number from variants like
# "Art. 6 (1) GDPR", "Article 6(1)(a) GDPR", "Art. 6 GDPR", "(6) GDPR"
_ART_NUM_RE = re.compile(r'(?:Art(?:icle)?\.?\s*)(\d+)', re.IGNORECASE)


def canonical_article(raw: str) -> str | None:
    """
    Returns 'Article N' from raw strings like 'Art. 6 (1) GDPR'.
    Returns None for non-article strings like '(2) GDPR', 'Unknown', 'c) GDPR'.
    """
    m = _ART_NUM_RE.search(raw)
    if not m:
        return None
    n = int(m.group(1))
    # GDPR has articles 1-99; filter noise
    if n < 1 or n > 99:
        return None
    return f"Article {n}"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def fetch_article_cases(
    cur: psycopg.Cursor,
    canonical: str,
    limit: int = 20,
) -> list[dict]:
    """
    Returns up to `limit` cases that reference this canonical article.
    Matches any raw string that normalises to `canonical`.
    """
    # Extract article number from canonical string ("Article 32" → 32)
    n = int(canonical.split()[-1])

    cur.execute(
        """
        SELECT d.title, d.authority, d.decision_year,
               d.fine_amount, d.fine_currency, d.controller_name,
               d.summary_facts
        FROM   documents d
        WHERE  EXISTS (
            SELECT 1 FROM unnest(d.gdpr_articles) a
            WHERE a ~ %s
        )
        AND d.summary_facts IS NOT NULL
        ORDER BY d.fine_amount DESC NULLS LAST, d.decision_year DESC NULLS LAST
        LIMIT  %s
        """,
        (rf"(?i)Art(?:icle)?\.?\s*{n}\b", limit),
    )
    rows = cur.fetchall()
    return [
        {
            "title":      row[0] or "",
            "authority":  row[1] or "",
            "year":       row[2],
            "fine":       row[3],
            "currency":   row[4] or "EUR",
            "controller": row[5] or "",
            "facts":      (row[6] or "")[:400],
        }
        for row in rows
    ]


def count_cases_by_canonical(cur: psycopg.Cursor) -> dict[str, int]:
    """Returns {canonical_article: case_count} for all articles in DB."""
    cur.execute(
        """
        SELECT art, count(*) AS n
        FROM (
            SELECT unnest(gdpr_articles) AS art
            FROM documents
            WHERE gdpr_articles IS NOT NULL
        ) t
        GROUP BY art
        """
    )
    counts: dict[str, int] = {}
    for raw_art, n in cur.fetchall():
        canon = canonical_article(raw_art)
        if canon:
            counts[canon] = counts.get(canon, 0) + n
    return counts


# ── LLM summary generation ─────────────────────────────────────────────────────

_SUMMARY_PROMPT = """\
You are analyzing GDPR enforcement data. Based on the {n} cases provided involving \
{article} of the GDPR, write 3-4 sentences describing:
1. What types of violations typically trigger {article} enforcement
2. Which sectors or industries are most affected
3. What fine ranges are typical (use specific amounts from the data)
4. Any notable patterns, trends, or landmark precedents

Be specific and cite concrete examples from the provided data.
Do not add general legal commentary — only describe patterns visible in the data.

Cases:
{cases_text}

Write your summary now (3-4 sentences, factual, data-driven):"""


def _format_cases_for_prompt(cases: list[dict]) -> str:
    lines = []
    for c in cases[:15]:  # cap at 15 to stay within token budget
        fine_str = f"€{c['fine']:,}" if c["fine"] else "no fine recorded"
        lines.append(
            f"- {c['title']} | {c['authority']} | {c['year']} | {fine_str}\n"
            f"  {c['facts'][:200]}"
        )
    return "\n".join(lines)


def generate_summary(
    ac: anthropic.Anthropic,
    article: str,
    cases: list[dict],
) -> str:
    cases_text = _format_cases_for_prompt(cases)
    prompt = _SUMMARY_PROMPT.format(
        n=len(cases),
        article=article,
        cases_text=cases_text,
    )
    msg = ac.messages.create(
        model=MODEL_HAIKU,
        max_tokens=300,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


# ── Corpus index I/O ────────────────────────────────────────────────────────────

def load_corpus_index() -> dict:
    if CORPUS_INDEX_PATH.exists():
        return json.loads(CORPUS_INDEX_PATH.read_text(encoding="utf-8"))
    return {}


def save_corpus_index(ci: dict) -> None:
    CORPUS_INDEX_PATH.write_text(
        json.dumps(ci, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL no configurado.")
        sys.exit(1)
    if not ANTHROPIC_API_KEY and not args.dry_run:
        log.error("ANTHROPIC_API_KEY no configurado.")
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    log.info("Conectado a CockroachDB.")

    with conn.cursor() as cur:
        all_counts = count_cases_by_canonical(cur)

    # Filter and sort by case count
    min_cases = args.min_cases
    eligible = sorted(
        [(art, n) for art, n in all_counts.items() if n >= min_cases],
        key=lambda x: x[1],
        reverse=True,
    )

    # Optionally restrict to specific articles
    if args.articles:
        wanted = {canonical_article(a) or a for a in args.articles.split(",")}
        eligible = [(art, n) for art, n in eligible if art in wanted]

    log.info("Artículos elegibles (>=%d casos): %d", min_cases, len(eligible))
    for art, n in eligible:
        log.info("  %-12s  %d casos", art, n)

    if args.dry_run:
        log.info("--dry-run: sin llamadas LLM ni escrituras.")
        conn.close()
        return

    ci = load_corpus_index()
    existing_summaries: dict = ci.setdefault("article_summaries", {})

    ac = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n_generated = n_skipped = 0

    with conn.cursor() as cur:
        for article, n_cases in eligible:
            if article in existing_summaries and not args.force:
                log.info("Saltando %s (ya existe, usa --force para regenerar)", article)
                n_skipped += 1
                continue

            log.info("Generando summary para %s (%d casos)...", article, n_cases)
            cases = fetch_article_cases(cur, article, limit=20)
            if not cases:
                log.warning("  Sin casos con summary_facts — omitiendo.")
                continue

            fines = [c["fine"] for c in cases if c["fine"]]
            top_sectors_raw = [c["authority"] for c in cases if c["authority"]]
            authorities: list[str] = sorted(
                set(top_sectors_raw), key=lambda a: top_sectors_raw.count(a), reverse=True
            )[:5]

            try:
                summary_text = generate_summary(ac, article, cases)
            except Exception as exc:
                log.warning("  LLM error para %s: %s — omitiendo.", article, exc)
                continue

            existing_summaries[article] = {
                "n_cases":         n_cases,
                "summary":         summary_text,
                "top_authorities": authorities,
                "fine_range": {
                    "min": min(fines) if fines else None,
                    "max": max(fines) if fines else None,
                },
            }
            n_generated += 1
            log.info("  Summary generado (%d chars).", len(summary_text))

    conn.close()
    save_corpus_index(ci)

    log.info("=" * 55)
    log.info("Completado.")
    log.info("  Summaries generados: %d", n_generated)
    log.info("  Omitidos (ya existían): %d", n_skipped)
    log.info("  Corpus index actualizado: %s", CORPUS_INDEX_PATH)
    log.info("Siguiente: PYTHONUTF8=1 python db/build_corpus_index.py  (regenera el índice base)")


def main() -> None:
    parser = argparse.ArgumentParser(description="JurisMind — Article summaries builder")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Mostrar artículos elegibles sin llamar LLM")
    parser.add_argument("--min-cases",  type=int, default=MIN_CASES_DEFAULT,
                        help=f"Mínimo de casos para generar summary (default: {MIN_CASES_DEFAULT})")
    parser.add_argument("--articles",   default=None,
                        help="Solo procesar estos artículos (ej: 'Art. 6,Art. 32')")
    parser.add_argument("--force",      action="store_true",
                        help="Regenerar summaries aunque ya existan")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
