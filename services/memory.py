"""
Memory service — save/retrieve org profiles and research sessions.
Uses existing user_memory and research_sessions tables.
"""
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import psycopg

log = logging.getLogger(__name__)


# ── Org Profile ──

def save_org_profile(
    conn: psycopg.Connection, user_id: str, profile: dict[str, Any]
) -> str:
    """Save or update organization profile in user_memory."""
    cur = conn.cursor()

    # Check if profile already exists
    cur.execute("""
        SELECT id FROM user_memory
        WHERE user_id = %s AND memory_type = 'org_profile'
        LIMIT 1
    """, (user_id,))
    existing = cur.fetchone()

    now = datetime.now(timezone.utc)
    if existing:
        cur.execute("""
            UPDATE user_memory
            SET content = %s, context = %s,
                updated_at = %s, last_accessed_at = %s, access_count = access_count + 1
            WHERE id = %s
        """, (
            json.dumps(profile),
            json.dumps({"source": "privacy_policy_scraper"}),
            now, now, existing[0],
        ))
        log.info("Updated org profile for user %s", user_id)
        return str(existing[0])
    else:
        mem_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO user_memory
                (id, user_id, memory_type, content, context,
                 importance_score, access_count, last_accessed_at,
                 created_at, updated_at)
            VALUES (%s, %s, 'org_profile', %s, %s, 1.0, 1, %s, %s, %s)
        """, (
            mem_id, user_id, json.dumps(profile),
            json.dumps({"source": "privacy_policy_scraper"}),
            now, now, now,
        ))
        log.info("Created org profile for user %s", user_id)
        return mem_id


def get_org_profile(conn: psycopg.Connection, user_id: str) -> dict[str, Any] | None:
    """Retrieve organization profile, updating access tracking."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE user_memory
        SET last_accessed_at = %s, access_count = access_count + 1
        WHERE user_id = %s AND memory_type = 'org_profile'
        RETURNING content
    """, (datetime.now(timezone.utc), user_id))
    row = cur.fetchone()
    if row:
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return None


# ── Research Sessions ──

def save_research_session(
    conn: psycopg.Connection,
    user_id: str,
    query: str,
    intent: str,
    filters: dict[str, Any],
    results_summary: dict[str, Any],
) -> str:
    """Save a research session."""
    cur = conn.cursor()
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    cur.execute("""
        INSERT INTO research_sessions
            (id, user_id, query, title, session_context, started_at, ended_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        session_id, user_id, query,
        query[:100],  # title = truncated query
        json.dumps({"intent": intent, "filters": filters, "results": results_summary}),
        now, now,
    ))
    log.info("Saved research session %s for user %s", session_id, user_id)
    return session_id


def get_research_history(
    conn: psycopg.Connection, user_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Get recent research sessions for a user."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, query, title, session_context, started_at
        FROM research_sessions
        WHERE user_id = %s
        ORDER BY started_at DESC
        LIMIT %s
    """, (user_id, limit))
    return [
        {
            "id": str(r[0]),
            "query": r[1],
            "title": r[2],
            "context": r[3],
            "timestamp": str(r[4]) if r[4] else None,
        }
        for r in cur.fetchall()
    ]


def get_updates_since(
    conn: psycopg.Connection, user_id: str, since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Find new decisions matching user's past research patterns.

    Looks at the user's past queries to extract jurisdictions and articles,
    then finds new decisions added since `since` that match.
    """
    cur = conn.cursor()

    # Get user's last visit if not provided
    if since is None:
        cur.execute("""
            SELECT max(started_at) FROM research_sessions
            WHERE user_id = %s
        """, (user_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return []
        since = row[0]

    # Get user's org profile for jurisdiction/sector
    profile = get_org_profile(conn, user_id)

    # Get jurisdictions and articles from past research
    cur.execute("""
        SELECT session_context
        FROM research_sessions
        WHERE user_id = %s
        ORDER BY started_at DESC LIMIT 10
    """, (user_id,))
    past_sessions = cur.fetchall()

    jurisdictions = set()
    articles = set()

    # From org profile
    if profile:
        if profile.get("jurisdiction"):
            jurisdictions.add(profile["jurisdiction"])

    # From past research filters
    for (ctx,) in past_sessions:
        if not ctx:
            continue
        filters = ctx.get("filters", {}) if isinstance(ctx, dict) else {}
        if filters.get("jurisdiction"):
            jurisdictions.add(filters["jurisdiction"])
        for art in filters.get("articles", []):
            articles.add(art)

    if not jurisdictions and not articles:
        return []

    # Find new decisions matching those patterns
    conditions = []
    params: list[Any] = []

    if jurisdictions:
        conditions.append("jurisdiction = ANY(%s)")
        params.append(list(jurisdictions))
    if articles:
        conditions.append("gdpr_articles && %s")
        params.append(list(articles))

    where = " OR ".join(conditions) if conditions else "TRUE"
    params.append(since)

    cur.execute(f"""
        SELECT id, title, jurisdiction, fine_amount, decision_date, gdpr_articles
        FROM documents
        WHERE ({where})
          AND COALESCE(updated_at, decision_date) > %s
        ORDER BY decision_date DESC NULLS LAST
        LIMIT 10
    """, params)

    return [
        {
            "id": str(r[0]),
            "title": r[1],
            "jurisdiction": r[2],
            "fine": r[3],
            "date": str(r[4]) if r[4] else None,
            "articles": r[5] or [],
        }
        for r in cur.fetchall()
    ]


def get_last_visit(conn: psycopg.Connection, user_id: str) -> datetime | None:
    """Get the user's last research session timestamp."""
    cur = conn.cursor()
    cur.execute("""
        SELECT max(started_at) FROM research_sessions
        WHERE user_id = %s
    """, (user_id,))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


# ── CLI test ──
if __name__ == "__main__":
    import os
    import sys

    logging.basicConfig(level=logging.INFO)
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

    test_user = "test-user-001"

    # Save an org profile
    profile = {
        "company_url": "https://stripe.com",
        "company_name": "Stripe",
        "jurisdiction": "United States",
        "sector": "Finance",
        "data_types": ["financial", "contact", "identification"],
        "legal_bases": ["consent", "contract", "legitimate_interest"],
        "transfers": [{"destination": "EU", "mechanism": "SCCs"}],
        "special_categories": False,
    }
    mem_id = save_org_profile(conn, test_user, profile)
    print(f"Saved org profile: {mem_id}")

    # Retrieve it
    retrieved = get_org_profile(conn, test_user)
    print(f"Retrieved: {json.dumps(retrieved, indent=2)}")

    # Save a research session
    sess_id = save_research_session(
        conn, test_user,
        query="Art. 32 data breach in healthcare Spain",
        intent="simulate",
        filters={"jurisdiction": "Spain", "articles": ["Art. 32 GDPR", "Art. 33 GDPR"]},
        results_summary={"count": 129, "median_fine": 9914},
    )
    print(f"\nSaved research session: {sess_id}")

    # Get history
    history = get_research_history(conn, test_user)
    print(f"\nResearch history ({len(history)} sessions):")
    for h in history:
        print(f"  [{h['timestamp']}] {h['query']}")

    # Check updates since
    updates = get_updates_since(conn, test_user)
    print(f"\nUpdates since last visit: {len(updates)}")
    for u in updates:
        print(f"  {u['jurisdiction']} | EUR {u['fine']:,} | {u['title'][:60]}")

    # Cleanup test data
    cur = conn.cursor()
    cur.execute("DELETE FROM user_memory WHERE user_id = %s", (test_user,))
    cur.execute("DELETE FROM research_sessions WHERE user_id = %s", (test_user,))
    print(f"\nCleaned up test data for {test_user}")

    conn.close()
