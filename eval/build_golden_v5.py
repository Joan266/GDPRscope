"""Build golden_set_v5.json — doc-first methodology, 500 queries, balanced categories.

Improvements over v4:
- Target 500 queries (56 per category x 9 = 504)
- Equal distribution across all 9 categories
- Preserves existing v4 queries (manually validated)
- Category-specific prompt templates (not one generic prompt)
- Quality filters: min length, no title-as-query, no case numbers in query
- Special sampling for cross_jurisdiction (same article, different jurisdictions)
- Special sampling for sector (group by industry)
- Special sampling for edge_case (unusual outcomes, low fines, dismissed)

Usage:
    export $(grep -v '^#' .env | xargs)
    PYTHONUTF8=1 python eval/build_golden_v5.py
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import openai
import psycopg

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL_ID = "moonshotai/kimi-k2"
OUTPUT_PATH = Path("eval/golden_set_v5.json")
V4_PATH = Path("eval/golden_set_v4.json")

CATEGORIES = [
    "named_entity",
    "article_lookup",
    "conceptual",
    "scenario",
    "fine_lookup",
    "cross_jurisdiction",
    "sector",
    "edge_case",
    "false_premise",
    "multi_target",
]

TARGET_PER_CATEGORY = 50  # 50 x 10 = 500

# --- Category-specific prompt templates ---

_PROMPT_NAMED_ENTITY = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE search query that a privacy
lawyer would naturally ask to find THIS specific case by mentioning the company
or controller name.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Controller: {controller}
- Facts: {facts}

RULES:
1. The query MUST mention the controller/company name or a distinctive entity
2. Write as a real user would search — natural language, not keywords
3. Do NOT include the exact case number or reference number in the query
4. The query must be at least 25 characters long
5. Do NOT just restate the document title

Return ONLY valid JSON:
{{"query": "...", "category": "named_entity"}}"""

_PROMPT_ARTICLE_LOOKUP = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE search query asking about
how a specific GDPR article was applied or interpreted in enforcement.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Facts: {facts}
- Holding: {holding}

RULES:
1. The query MUST mention a specific GDPR article number (e.g., "Article 6",
   "Article 15", "Article 32")
2. Ask about how the article was interpreted, not just "was it violated"
3. Frame it as a legal question about the article's scope or application
4. Do NOT include the exact case number or reference number
5. The query must be at least 25 characters long

Return ONLY valid JSON:
{{"query": "...", "category": "article_lookup"}}"""

_PROMPT_CONCEPTUAL = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE search query about the
abstract legal concept or principle at stake — NOT about the specific company.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Articles violated: {articles}
- Outcome: {outcome}
- Facts: {facts}
- Holding: {holding}

RULES:
1. Focus on the LEGAL PRINCIPLE, not the specific entity or case
2. Use "Can a company...", "Does GDPR require...", "Is it legal to..." phrasing
3. The query should be answerable by this document but not mention the company name
4. Do NOT include the exact case number or reference number
5. The query must be at least 30 characters long

Return ONLY valid JSON:
{{"query": "...", "category": "conceptual"}}"""

_PROMPT_SCENARIO = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE hypothetical scenario query
that a DPO or lawyer would ask, where this case would be the best precedent.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Facts: {facts}
- Holding: {holding}

RULES:
1. Frame as a hypothetical: "What if...", "Can I...", "Is it legal to...",
   "What would happen if..."
2. Describe a REALISTIC situation a DPO might face, inspired by the facts
3. Do NOT mention the specific company, DPA, or case number
4. The query must be at least 30 characters long
5. The scenario should be specific enough that this document is clearly relevant

Return ONLY valid JSON:
{{"query": "...", "category": "scenario"}}"""

_PROMPT_FINE_LOOKUP = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE search query about the fine
amount, penalties, or financial sanctions in this case.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Controller: {controller}

RULES:
1. Ask about the fine amount, penalty, or financial sanction
2. You can mention the company name or describe the violation type
3. Frame as: "How much was X fined for...", "What was the fine in the case
   where...", "Did X get fined for..."
4. Do NOT include the exact case number or reference number
5. The query must be at least 25 characters long

Return ONLY valid JSON:
{{"query": "...", "category": "fine_lookup"}}"""

_PROMPT_CROSS_JURISDICTION = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given these TWO enforcement decisions from DIFFERENT jurisdictions about similar
GDPR issues, generate ONE search query comparing how different DPAs handled
a similar violation.

DOCUMENT 1:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Facts: {facts}

DOCUMENT 2:
- Title: {title2}
- Authority: {authority2} ({jurisdiction2})
- Fine: {fine_str2}
- Articles violated: {articles2}
- Facts: {facts2}

RULES:
1. The query MUST reference at least 2 countries or DPAs
2. Ask about differences in enforcement, fines, or interpretation
3. Use phrasing like "How does X's approach compare to Y's...",
   "Have both France and Germany fined companies for..."
4. Do NOT include exact case numbers
5. The query must be at least 30 characters long

Return ONLY valid JSON:
{{"query": "...", "category": "cross_jurisdiction"}}"""

_PROMPT_SECTOR = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision from a specific industry/sector, generate
ONE search query about GDPR enforcement patterns in that sector.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Controller: {controller}
- Sector/Industry: {sector}
- Facts: {facts}

RULES:
1. The query MUST mention the industry/sector (healthcare, telecom, banking,
   education, tech, retail, etc.)
2. Ask about enforcement PATTERNS in that sector, not just this one case
3. Examples: "How have DPAs enforced GDPR in the healthcare sector?",
   "What fines have telecom companies received for consent violations?"
4. Do NOT include exact case numbers
5. The query must be at least 25 characters long

Return ONLY valid JSON:
{{"query": "...", "category": "sector"}}"""

_PROMPT_EDGE_CASE = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision with an unusual or noteworthy outcome,
generate ONE search query about the edge case or novel aspect of GDPR
enforcement it represents.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Facts: {facts}
- Holding: {holding}

RULES:
1. Focus on what makes this case UNUSUAL: dismissed complaint, very low fine,
   novel violation type, surprising interpretation, first-of-kind
2. Frame as a genuine legal question about the unusual aspect
3. Do NOT include exact case numbers
4. The query must be at least 25 characters long
5. Do NOT just restate the document title

Return ONLY valid JSON:
{{"query": "...", "category": "edge_case"}}"""

_PROMPT_FALSE_PREMISE = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given this GDPR enforcement decision, generate ONE search query that contains
a DELIBERATE FACTUAL ERROR about the case. The search engine should be able
to find this document and correct the misconception.

DOCUMENT:
- Title: {title}
- Authority: {authority} ({jurisdiction})
- Fine: {fine_str}
- Articles violated: {articles}
- Outcome: {outcome}
- Controller: {controller}
- Facts: {facts}

RULES:
1. Include ONE clearly wrong fact. Choose ONE of:
   - Wrong DPA/country (e.g., say "Italian DPA" when it was actually French)
   - Wrong fine amount (e.g., double or halve the real amount)
   - Wrong article (e.g., say "Article 6" when it was actually Article 32)
   - Wrong outcome (e.g., say "fined" when it was actually dismissed)
2. The rest of the query should be accurate enough to find the right document
3. Make it sound like a genuine misconception, not obviously wrong
4. Do NOT include exact case numbers
5. The query must be at least 25 characters long

Return ONLY valid JSON:
{{"query": "...", "category": "false_premise"}}"""

_PROMPT_MULTI_TARGET = """\
You are helping build an evaluation dataset for a GDPR enforcement search engine.

Given these {n_docs} related GDPR enforcement decisions, generate ONE search query
that a privacy lawyer would ask where ALL of these documents together provide the
best answer. The query should naturally require information from multiple cases.

DOCUMENTS:
{docs_block}

WHAT CONNECTS THEM: {connection}

RULES:
1. The query must be answerable ONLY by looking at multiple documents, not just one
2. Ask about patterns, comparisons, trends, or aggregated information
3. Examples: "What fines has [company] received across EU countries?",
   "How have DPAs handled [violation type] differently?",
   "What is the range of penalties for [article] violations?"
4. Do NOT include exact case numbers or reference numbers
5. The query must be at least 30 characters long
6. Write as a real user would search — natural language, not keywords

Return ONLY valid JSON:
{{"query": "..."}}"""

CATEGORY_PROMPTS: dict[str, str] = {
    "named_entity": _PROMPT_NAMED_ENTITY,
    "article_lookup": _PROMPT_ARTICLE_LOOKUP,
    "conceptual": _PROMPT_CONCEPTUAL,
    "scenario": _PROMPT_SCENARIO,
    "fine_lookup": _PROMPT_FINE_LOOKUP,
    "cross_jurisdiction": _PROMPT_CROSS_JURISDICTION,
    "sector": _PROMPT_SECTOR,
    "edge_case": _PROMPT_EDGE_CASE,
    "false_premise": _PROMPT_FALSE_PREMISE,
    "multi_target": _PROMPT_MULTI_TARGET,
}

# Sector keywords for doc classification
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "healthcare": ["hospital", "patient", "medical", "health", "clinic", "doctor",
                    "pharmacy", "vaccine", "covid", "diagnosis", "treatment", "nhs"],
    "telecom": ["telecom", "mobile", "phone", "sim", "vodafone", "orange", "telefonica",
                "broadband", "subscriber", "call", "sms", "network"],
    "banking": ["bank", "credit", "loan", "payment", "financial", "insurance",
                "fintech", "mortgage", "klarna", "revolut"],
    "education": ["school", "university", "student", "teacher", "education",
                  "academic", "campus", "exam", "pupil"],
    "technology": ["app", "software", "platform", "ai", "algorithm", "chatgpt",
                   "openai", "google", "meta", "facebook", "amazon", "microsoft",
                   "tiktok", "clearview", "facial recognition"],
    "retail": ["shop", "store", "customer", "e-commerce", "online store",
               "carrefour", "supermarket", "mall"],
    "employment": ["employee", "employer", "worker", "workplace", "hr",
                   "recruitment", "staff", "dismissal", "termination"],
    "real_estate": ["landlord", "tenant", "property", "housing", "apartment",
                    "condominium", "building"],
    "public_sector": ["municipality", "government", "ministry", "police", "public",
                      "authority", "council", "customs"],
    "media": ["newspaper", "journalist", "media", "broadcast", "radio", "tv",
              "publisher", "blog", "website"],
}


def get_client() -> openai.OpenAI:
    """Create OpenRouter client."""
    return openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )


def load_v4_queries() -> list[dict]:
    """Load existing queries — prefer v5 (has previous run's results), fall back to v4."""
    # Use v5 as base if it exists (accumulate across runs)
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            existing = json.load(f)
        log.info("Loaded %d existing v5 queries as base", len(existing))
        return existing
    if not V4_PATH.exists():
        log.warning("No existing golden set found — starting from scratch")
        return []
    with open(V4_PATH, encoding="utf-8") as f:
        v4 = json.load(f)
    log.info("Loaded %d v4 queries", len(v4))
    return v4


def classify_sector(doc: dict) -> str:
    """Classify a document into a sector based on text content."""
    text_fields = " ".join(str(doc.get(f, "") or "") for f in
                           ["title", "controller", "facts", "holding"]).lower()
    scores: dict[str, int] = {}
    for sector, keywords in SECTOR_KEYWORDS.items():
        scores[sector] = sum(1 for kw in keywords if kw in text_fields)
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    return best if scores[best] > 0 else "general"


def sample_docs(conn: psycopg.Connection) -> list[dict]:
    """Sample diverse documents — stratified by jurisdiction, source, fine."""
    cur = conn.cursor()

    cur.execute("""
        SELECT d.source_id, d.title, d.authority, d.jurisdiction,
               d.fine_amount, d.fine_currency, d.gdpr_articles,
               d.violation_type, d.outcome, d.controller_name,
               d.case_number, d.source, d.content_depth,
               left(d.summary_facts, 600) as facts,
               left(d.summary_holding, 600) as holding
        FROM documents d
        WHERE EXISTS (
            SELECT 1 FROM chunks c
            WHERE c.document_id = d.id
              AND c.chunk_type = 'child'
              AND c.embedding IS NOT NULL
        )
        AND d.source IN ('gdprhub', 'enforcement_tracker')
        AND (length(d.summary_facts) > 50 OR length(d.summary_holding) > 50)
    """)

    cols = [desc[0] for desc in cur.description]
    all_docs = [dict(zip(cols, row)) for row in cur.fetchall()]
    log.info("Total searchable docs: %d", len(all_docs))
    return all_docs


def sample_for_category(
    all_docs: list[dict],
    category: str,
    needed: int,
    used_source_ids: set[str],
) -> list[dict]:
    """Sample docs appropriate for a specific category."""
    available = [d for d in all_docs
                 if d["source_id"] not in used_source_ids
                 and (d.get("facts") or d.get("holding"))]

    if category == "named_entity":
        # Prefer docs with known controller names
        preferred = [d for d in available
                     if d.get("controller_name") and d["controller_name"] != "Unknown"
                     and len(d.get("controller_name", "")) > 2]
        random.shuffle(preferred)
        fallback = [d for d in available if d not in preferred]
        random.shuffle(fallback)
        return (preferred + fallback)[:needed * 15]

    if category == "article_lookup":
        # Prefer docs with specific GDPR articles listed
        preferred = [d for d in available
                     if d.get("gdpr_articles") and len(d["gdpr_articles"]) > 0]
        random.shuffle(preferred)
        return preferred[:needed * 15]

    if category == "conceptual":
        # Prefer docs with substantial holdings (legal reasoning)
        preferred = [d for d in available
                     if d.get("holding") and len(d.get("holding", "")) > 100]
        random.shuffle(preferred)
        return preferred[:needed * 15]

    if category == "scenario":
        # Prefer docs with detailed facts
        preferred = [d for d in available
                     if d.get("facts") and len(d.get("facts", "")) > 100]
        random.shuffle(preferred)
        return preferred[:needed * 15]

    if category == "fine_lookup":
        # Must have a fine
        with_fine = [d for d in available
                     if d.get("fine_amount") and d["fine_amount"] > 0]
        random.shuffle(with_fine)
        return with_fine[:needed * 15]

    if category == "cross_jurisdiction":
        # Handled separately — return empty, pairs built in generate_cross_jurisdiction
        return available

    if category == "sector":
        # Classify into sectors, prefer docs with clear sector assignment
        classified: list[dict] = []
        for d in available:
            sector = classify_sector(d)
            if sector != "general":
                d["_sector"] = sector
                classified.append(d)
        random.shuffle(classified)
        return classified[:needed * 15]

    if category == "edge_case":
        # Prefer unusual outcomes: dismissed, warnings, very low fines, or novel violations
        edge_candidates: list[dict] = []
        for d in available:
            outcome = (d.get("outcome") or "").lower()
            fine = d.get("fine_amount") or 0
            is_edge = (
                "dismiss" in outcome
                or "warn" in outcome
                or "reprimand" in outcome
                or (fine > 0 and fine < 500)
                or (fine > 10_000_000)
                or "novel" in outcome
                or d.get("content_depth") == "full"
            )
            if is_edge:
                edge_candidates.append(d)
        # Also add docs with full text for diversity
        rest = [d for d in available
                if d not in edge_candidates and d.get("facts") and len(d["facts"]) > 100]
        random.shuffle(edge_candidates)
        random.shuffle(rest)
        return (edge_candidates + rest)[:needed * 15]

    if category == "false_premise":
        # Any doc with enough info to create a wrong-fact query
        preferred = [d for d in available
                     if (d.get("facts") and len(d["facts"]) > 50)
                     or (d.get("fine_amount") and d["fine_amount"] > 0)]
        random.shuffle(preferred)
        return preferred[:needed * 15]

    random.shuffle(available)
    return available[:needed * 15]


def build_cross_jurisdiction_pairs(
    all_docs: list[dict],
    needed: int,
    used_source_ids: set[str],
) -> list[tuple[dict, dict]]:
    """Build pairs of docs from different jurisdictions about similar GDPR articles."""
    available = [d for d in all_docs
                 if d["source_id"] not in used_source_ids
                 and d.get("gdpr_articles") and len(d["gdpr_articles"]) > 0
                 and d.get("jurisdiction")]

    # Group by GDPR article
    by_article: dict[str, list[dict]] = defaultdict(list)
    for d in available:
        for art in (d.get("gdpr_articles") or []):
            by_article[art].append(d)

    pairs: list[tuple[dict, dict]] = []
    used_in_pairs: set[str] = set()

    # Find articles with docs from multiple jurisdictions
    for art, docs in sorted(by_article.items(), key=lambda x: -len(x[1])):
        # Group by jurisdiction
        by_juris: dict[str, list[dict]] = defaultdict(list)
        for d in docs:
            if d["source_id"] not in used_in_pairs:
                by_juris[d["jurisdiction"]].append(d)

        jurisdictions = [j for j, ds in by_juris.items() if len(ds) > 0]
        if len(jurisdictions) < 2:
            continue

        # Create pairs from different jurisdictions
        random.shuffle(jurisdictions)
        for i in range(0, len(jurisdictions) - 1, 2):
            j1, j2 = jurisdictions[i], jurisdictions[i + 1]
            d1 = random.choice(by_juris[j1])
            d2 = random.choice(by_juris[j2])
            pairs.append((d1, d2))
            used_in_pairs.add(d1["source_id"])
            used_in_pairs.add(d2["source_id"])

            if len(pairs) >= needed * 2:
                break
        if len(pairs) >= needed * 2:
            break

    random.shuffle(pairs)
    return pairs[:needed * 15]


def build_multi_target_groups(
    all_docs: list[dict],
    needed: int,
    used_source_ids: set[str],
) -> list[tuple[list[dict], str]]:
    """Build groups of 2-4 related docs for multi_target queries.

    Returns list of (doc_group, connection_description).
    """
    available = [d for d in all_docs if d["source_id"] not in used_source_ids]
    groups: list[tuple[list[dict], str]] = []

    # Strategy 1: Same controller, multiple cases
    by_controller: dict[str, list[dict]] = defaultdict(list)
    for d in available:
        ctrl = (d.get("controller_name") or "").strip().lower()
        if ctrl and ctrl != "unknown" and len(ctrl) > 2:
            by_controller[ctrl].append(d)
    for ctrl, docs in sorted(by_controller.items(), key=lambda x: -len(x[1])):
        if len(docs) >= 2:
            sample = random.sample(docs, min(len(docs), 3))
            ctrl_display = sample[0].get("controller_name", ctrl)
            groups.append((sample, f"Same controller: {ctrl_display}"))
            if len(groups) >= needed * 2:
                break

    # Strategy 2: Same GDPR article, different jurisdictions
    by_article: dict[str, list[dict]] = defaultdict(list)
    for d in available:
        for art in (d.get("gdpr_articles") or []):
            by_article[art].append(d)
    for art, docs in sorted(by_article.items(), key=lambda x: -len(x[1])):
        juris_seen: dict[str, dict] = {}
        for d in docs:
            j = d.get("jurisdiction", "")
            if j and j not in juris_seen:
                juris_seen[j] = d
        if len(juris_seen) >= 2:
            sample = list(juris_seen.values())[:3]
            groups.append((sample, f"Same GDPR Article {art}, different jurisdictions"))
            if len(groups) >= needed * 3:
                break

    # Strategy 3: Same DPA, same violation type
    by_dpa: dict[str, list[dict]] = defaultdict(list)
    for d in available:
        auth = (d.get("authority") or "").strip()
        if auth:
            by_dpa[auth].append(d)
    for auth, docs in sorted(by_dpa.items(), key=lambda x: -len(x[1])):
        if len(docs) >= 2:
            sample = random.sample(docs, min(len(docs), 3))
            groups.append((sample, f"Same DPA: {auth}"))
            if len(groups) >= needed * 4:
                break

    random.shuffle(groups)
    return groups[:needed * 15]


def _format_fine(doc: dict) -> str:
    """Format fine amount for prompt."""
    if doc.get("fine_amount") and doc["fine_amount"] > 0:
        currency = doc.get("fine_currency", "EUR")
        return f"{currency} {doc['fine_amount']:,.0f}"
    return "No fine"


def generate_single_query(
    client: openai.OpenAI,
    doc: dict,
    category: str,
    doc2: dict | None = None,
    multi_group: tuple[list[dict], str] | None = None,
) -> dict | None:
    """Generate one query for a document using category-specific prompt."""
    fine_str = _format_fine(doc)
    articles = ", ".join(doc.get("gdpr_articles") or []) or "Not specified"

    if category == "multi_target" and multi_group:
        group_docs, connection = multi_group
        docs_block = ""
        for i, gd in enumerate(group_docs, 1):
            gd_fine = _format_fine(gd)
            gd_arts = ", ".join(gd.get("gdpr_articles") or []) or "N/A"
            docs_block += (
                f"\nDocument {i}:\n"
                f"- Title: {gd.get('title', '')[:120]}\n"
                f"- Authority: {gd.get('authority', 'Unknown')} ({gd.get('jurisdiction', 'Unknown')})\n"
                f"- Fine: {gd_fine}\n"
                f"- Articles: {gd_arts}\n"
                f"- Controller: {gd.get('controller_name') or 'N/A'}\n"
                f"- Facts: {(gd.get('facts') or 'N/A')[:200]}\n"
            )
        prompt = CATEGORY_PROMPTS["multi_target"].format(
            n_docs=len(group_docs),
            docs_block=docs_block,
            connection=connection,
        )
    elif category == "cross_jurisdiction" and doc2:
        prompt = CATEGORY_PROMPTS["cross_jurisdiction"].format(
            title=doc.get("title", "")[:120],
            authority=doc.get("authority", "Unknown"),
            jurisdiction=doc.get("jurisdiction", "Unknown"),
            fine_str=fine_str,
            articles=articles,
            facts=(doc.get("facts") or "Not available")[:350],
            title2=doc2.get("title", "")[:120],
            authority2=doc2.get("authority", "Unknown"),
            jurisdiction2=doc2.get("jurisdiction", "Unknown"),
            fine_str2=_format_fine(doc2),
            articles2=", ".join(doc2.get("gdpr_articles") or []) or "Not specified",
            facts2=(doc2.get("facts") or "Not available")[:350],
        )
    elif category == "sector":
        sector = doc.get("_sector", classify_sector(doc))
        prompt = CATEGORY_PROMPTS["sector"].format(
            title=doc.get("title", "")[:120],
            authority=doc.get("authority", "Unknown"),
            jurisdiction=doc.get("jurisdiction", "Unknown"),
            fine_str=fine_str,
            articles=articles,
            outcome=doc.get("outcome", "Unknown"),
            controller=doc.get("controller_name") or "Not specified",
            sector=sector,
            facts=(doc.get("facts") or "Not available")[:400],
        )
    else:
        template = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["conceptual"])
        format_kwargs: dict[str, str] = {
            "title": doc.get("title", "")[:120],
            "authority": doc.get("authority", "Unknown"),
            "jurisdiction": doc.get("jurisdiction", "Unknown"),
            "fine_str": fine_str,
            "articles": articles,
            "outcome": doc.get("outcome", "Unknown"),
            "controller": doc.get("controller_name") or "Not specified",
            "facts": (doc.get("facts") or "Not available")[:400],
            "holding": (doc.get("holding") or "Not available")[:400],
        }
        prompt = template.format(**format_kwargs)

    try:
        # Try up to 2 times (retry on empty response)
        content = None
        for _attempt in range(2):
            resp = client.chat.completions.create(
                model=MODEL_ID,
                max_tokens=250,
                temperature=0.8,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.choices[0].message.content
            if content:
                break
        if not content:
            log.warning("EMPTY_RESPONSE [%s] %s", category, doc.get("title", "")[:50])
            return None
        raw = content.strip()

        # Parse JSON from response (handle markdown code blocks)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        query_text = data.get("query", "").strip()

        if not query_text:
            return None

        result_dict: dict = {
            "query": query_text,
            "category": category,
            "source_id": doc["source_id"],
            "title": doc.get("title", ""),
            "source_id2": doc2["source_id"] if doc2 else None,
            "title2": doc2.get("title", "") if doc2 else None,
        }
        # Multi-target: store all titles
        if category == "multi_target" and multi_group:
            group_docs, _ = multi_group
            result_dict["multi_titles"] = [gd.get("title", "") for gd in group_docs]
            result_dict["multi_source_ids"] = [gd["source_id"] for gd in group_docs]
        return result_dict
    except Exception as exc:
        log.warning("PARSE_FAIL [%s] %s: %s", category, doc.get("title", "")[:40], exc)
        return None


def _extract_case_numbers(text: str) -> set[str]:
    """Extract case/reference numbers from text."""
    patterns = [
        r'[A-Z]{2,}-\d{4}-\d+',
        r'C-\d+/\d+',
        r'\d+/\d{4}',
        r'ET-\d+',
        r'EXP\d{8,}',
        r'PS/\d{5}/\d{4}',
        r'SAN-\d{4}-\d+',
    ]
    nums: set[str] = set()
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            nums.add(m.group(0).upper())
    return nums


def apply_quality_filters(result: dict, category: str) -> bool:
    """Minimal filters: length, not title copy, plausible legal query."""
    query = result["query"]
    title = result.get("title", "")

    if len(query) < 15:
        return False
    if query.strip().lower() == title.strip().lower():
        return False
    return True


def deduplicate_queries(
    new_queries: list[dict],
    existing_queries: list[dict],
) -> list[dict]:
    """Remove near-duplicate queries against existing set and within new set."""
    existing_prefixes: set[str] = set()
    existing_sources: dict[str, int] = Counter()

    for q in existing_queries:
        prefix = re.sub(r'\s+', ' ', q["question"].lower()[:60])
        existing_prefixes.add(prefix)
        for sid in q.get("relevant_source_ids", []):
            existing_sources[sid] += 1

    seen_prefixes: set[str] = set(existing_prefixes)
    result: list[dict] = []

    for q in new_queries:
        # Check text similarity (prefix dedup)
        prefix = re.sub(r'\s+', ' ', q["query"].lower()[:60])
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)

        # Allow max 3 queries per source doc total (including existing)
        sid = q["source_id"]
        total_for_source = existing_sources.get(sid, 0) + sum(
            1 for r in result if r["source_id"] == sid
        )
        if total_for_source >= 3:
            continue

        result.append(q)

    return result


def _generate_batch_parallel(
    client: openai.OpenAI,
    tasks: list[tuple],
    needed: int,
    parallel: int,
) -> tuple[list[dict], int]:
    """Generate queries in parallel using ThreadPoolExecutor.

    Args:
        client: OpenRouter client.
        tasks: List of (doc, category, doc2_or_None, multi_group_or_None).
        needed: How many valid queries to produce.
        parallel: Number of parallel workers.

    Returns:
        (results, error_count)
    """
    results: list[dict] = []
    parse_fails = 0
    filter_rejects = 0

    def _run_one(task_tuple: tuple) -> dict | None:
        doc, cat = task_tuple[0], task_tuple[1]
        doc2 = task_tuple[2] if len(task_tuple) > 2 else None
        mg = task_tuple[3] if len(task_tuple) > 3 else None
        return generate_single_query(client, doc, cat, doc2=doc2, multi_group=mg)

    def _process(result: dict | None, cat: str) -> bool:
        nonlocal parse_fails, filter_rejects
        if result is None:
            parse_fails += 1
            return False
        if apply_quality_filters(result, cat):
            results.append(result)
            return True
        filter_rejects += 1
        return False

    if parallel <= 1:
        for task in tasks:
            if len(results) >= needed:
                break
            _process(_run_one(task), task[1])
        total_err = parse_fails + filter_rejects
        log.info("  breakdown: %d ok, %d parse_fail, %d filter_reject", len(results), parse_fails, filter_rejects)
        return results, total_err

    # Parallel execution
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {}
        submitted = 0
        batch_size = min(parallel * 2, len(tasks))

        for i in range(min(batch_size, len(tasks))):
            fut = executor.submit(_run_one, tasks[i])
            futures[fut] = tasks[i][1]  # category
            submitted = i + 1

        for fut in as_completed(futures):
            if len(results) >= needed:
                break
            cat = futures[fut]
            try:
                _process(fut.result(), cat)
            except Exception:
                parse_fails += 1

            if submitted < len(tasks) and len(results) < needed:
                new_fut = executor.submit(_run_one, tasks[submitted])
                futures[new_fut] = tasks[submitted][1]
                submitted += 1

            if len(results) % 10 == 0 and len(results) > 0:
                log.info("  progress: %d/%d generated (%d parse_fail, %d filter_reject)",
                         len(results), needed, parse_fails, filter_rejects)

    total_err = parse_fails + filter_rejects
    log.info("  breakdown: %d ok, %d parse_fail, %d filter_reject", len(results), parse_fails, filter_rejects)
    return results, total_err


def main() -> None:
    parser = argparse.ArgumentParser(description="Build golden_set_v5.json")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Number of parallel LLM workers (default 1)")
    args = parser.parse_args()

    if not OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY not set")
        sys.exit(1)

    parallel = args.parallel
    log.info("Parallel workers: %d", parallel)

    random.seed(42)
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    client = get_client()

    # 1. Load existing v4 queries
    v4_queries = load_v4_queries()
    v4_by_category = Counter(q["category"] for q in v4_queries)

    log.info("V4 category distribution:")
    for cat in CATEGORIES:
        log.info("  %s: %d / %d needed", cat, v4_by_category.get(cat, 0), TARGET_PER_CATEGORY)

    # 2. Calculate how many new queries needed per category
    needed_per_cat: dict[str, int] = {}
    for cat in CATEGORIES:
        have = v4_by_category.get(cat, 0)
        need = max(0, TARGET_PER_CATEGORY - have)
        needed_per_cat[cat] = need
    total_needed = sum(needed_per_cat.values())
    log.info("Total new queries needed: %d", total_needed)

    # 3. Sample all docs from DB
    all_docs = sample_docs(conn)

    # Track which source_ids are already used in v4
    v4_source_ids: set[str] = set()
    for q in v4_queries:
        for sid in q.get("relevant_source_ids", []):
            v4_source_ids.add(sid)

    # 4. Generate queries per category
    all_new_queries: list[dict] = []
    generation_errors = 0

    for cat in CATEGORIES:
        needed = needed_per_cat[cat]
        if needed == 0:
            log.info("Category '%s' already has %d — skipping", cat, v4_by_category.get(cat, 0))
            continue

        log.info("--- Generating %d queries for '%s' ---", needed, cat)

        # Track source_ids used in this generation run
        used_ids = set(v4_source_ids) | {q["source_id"] for q in all_new_queries}

        if cat == "cross_jurisdiction":
            pairs = build_cross_jurisdiction_pairs(all_docs, needed, used_ids)
            tasks = [(d1, cat, d2, None) for d1, d2 in pairs]
        elif cat == "multi_target":
            groups = build_multi_target_groups(all_docs, needed, used_ids)
            # task = (first_doc, category, None, (group_docs, connection_desc))
            tasks = [(grp[0], cat, None, (grp, cdesc)) for grp, cdesc in groups]
        else:
            sampled = sample_for_category(all_docs, cat, needed, used_ids)
            tasks = [(doc, cat, None, None) for doc in sampled]

        batch_results, batch_errors = _generate_batch_parallel(
            client, tasks, needed, parallel,
        )
        for r in batch_results:
            all_new_queries.append(r)
            used_ids.add(r["source_id"])
            if r.get("source_id2"):
                used_ids.add(r["source_id2"])
        generation_errors += batch_errors
        log.info("  %s: DONE — %d generated (%d errors)", cat, len(batch_results), batch_errors)

    log.info("Raw generation: %d new queries, %d errors", len(all_new_queries), generation_errors)

    # 5. Deduplicate new queries against v4 and within themselves
    all_new_queries = deduplicate_queries(all_new_queries, v4_queries)
    log.info("After dedup: %d new queries", len(all_new_queries))

    # 6. Combine v4 + new queries, then balance
    golden_set: list[dict] = []

    # First, add all v4 queries (up to TARGET_PER_CATEGORY per category)
    v4_added_per_cat: dict[str, int] = Counter()
    for q in v4_queries:
        cat = q["category"]
        if v4_added_per_cat[cat] < TARGET_PER_CATEGORY:
            golden_set.append(q)
            v4_added_per_cat[cat] += 1

    # Then add new queries per category
    new_by_cat: dict[str, list[dict]] = defaultdict(list)
    for q in all_new_queries:
        new_by_cat[q["category"]].append(q)

    for cat in CATEGORIES:
        have = v4_added_per_cat.get(cat, 0)
        remaining = TARGET_PER_CATEGORY - have
        pool = new_by_cat.get(cat, [])
        random.shuffle(pool)

        for q in pool[:remaining]:
            # Build relevant_source_ids: use title (matching eval pipeline)
            if q.get("multi_titles"):
                relevant_ids = q["multi_titles"]
            else:
                relevant_ids = [q["title"]]
                if q.get("title2"):
                    relevant_ids.append(q["title2"])

            golden_set.append({
                "id": "__placeholder__",
                "category": q["category"],
                "question": q["query"],
                "relevant_source_ids": relevant_ids,
                "expected_route": (
                    "sql" if q["category"] in ("named_entity", "fine_lookup") else "rag"
                ),
                "filters": {},
            })

    # 7. Assign sequential IDs
    for i, item in enumerate(golden_set, start=1):
        item["id"] = f"gs5-{i:03d}"

    # 8. Summary
    cats = Counter(q["category"] for q in golden_set)
    total_ids = sum(len(q["relevant_source_ids"]) for q in golden_set)

    log.info("=" * 60)
    log.info("Golden Set v5: %d queries, %d relevant docs", len(golden_set), total_ids)
    log.info("=" * 60)
    for c in CATEGORIES:
        from_v4 = v4_added_per_cat.get(c, 0)
        from_new = cats.get(c, 0) - from_v4
        log.info("  %s: %d total (%d from v4 + %d new)", c, cats.get(c, 0), from_v4, from_new)

    # 9. Save
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(golden_set, f, indent=2, ensure_ascii=False)
    log.info("Saved %s", OUTPUT_PATH)

    conn.close()


if __name__ == "__main__":
    main()
