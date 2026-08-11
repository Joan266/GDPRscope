"""
Privacy Policy Scraper + Organization Profile Extraction.
Scrapes a company's privacy policy page and uses LLM to extract
a structured organizational profile (jurisdiction, sector, data types, etc.)
"""
import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_POLICY_PATHS = [
    "/privacy-policy", "/privacy", "/legal/privacy",
    "/privacy-notice", "/data-protection", "/legal/privacy-policy",
    "/en/privacy-policy", "/en/privacy", "/en/privacy-notice",
    "/datenschutz", "/politica-de-privacidad", "/politique-de-confidentialite",
    "/en/privacy-statement.html", "/privacy-statement",
    "/en/legal/privacy", "/legal/data-protection",
    "/cookie-policy",  # sometimes combined
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_MAX_TEXT_CHARS = 15_000  # keep LLM costs low


@dataclass
class OrgProfile:
    company_url: str
    company_name: str | None
    jurisdiction: str | None
    sector: str | None
    data_types: list[str]
    legal_bases: list[str]
    transfers: list[dict[str, str]]
    special_categories: bool
    raw_snippet: str  # first 500 chars for verification


def scrape_privacy_policy(url: str) -> str | None:
    """Fetch and extract text from a company's privacy policy page."""
    base = url.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base

    # Try the URL directly first (user might paste the policy URL)
    urls_to_try = [base] + [base + path for path in _POLICY_PATHS]

    for try_url in urls_to_try:
        try:
            resp = requests.get(try_url, headers=_HEADERS, timeout=15,
                                allow_redirects=True)
            if resp.status_code != 200:
                continue
            text = _extract_text(resp.text)
            if _looks_like_privacy_policy(text):
                log.info("Found privacy policy at %s (%d chars)", try_url, len(text))
                return text[:_MAX_TEXT_CHARS]
        except requests.RequestException as e:
            log.debug("Failed to fetch %s: %s", try_url, e)
            continue

    log.warning("No privacy policy found for %s", url)
    return None


def _extract_text(html: str) -> str:
    """Extract main text content from HTML, stripping nav/footer/scripts."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                               "iframe", "noscript", "aside"]):
        tag.decompose()

    # Try to find main content area
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"content|privacy|policy|main", re.I))
        or soup.find("div", id=re.compile(r"content|privacy|policy|main", re.I))
        or soup.body
    )
    if not main:
        return ""

    text = main.get_text(separator="\n", strip=True)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_privacy_policy(text: str) -> bool:
    """Heuristic: does this text look like a privacy policy?"""
    if len(text) < 500:
        return False
    lower = text.lower()
    keywords = ["personal data", "privacy", "data protection", "gdpr",
                "cookies", "data subject", "controller", "processing",
                "datos personales", "proteccion de datos", "datenschutz",
                "donnees personnelles"]
    matches = sum(1 for kw in keywords if kw in lower)
    return matches >= 3


def extract_org_profile(policy_text: str, company_url: str) -> OrgProfile | None:
    """Use LLM to extract structured profile from privacy policy text."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set")
        return None

    prompt = _build_extraction_prompt(policy_text)

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        content = result["content"][0]["text"]
        return _parse_llm_response(content, company_url, policy_text[:500])
    except Exception as e:
        log.error("LLM extraction failed: %s", e)
        return None


def _build_extraction_prompt(policy_text: str) -> str:
    return f"""Analyze this privacy policy and extract structured information.
Return ONLY a JSON object with these fields:

{{
  "company_name": "string or null",
  "jurisdiction": "country name where the company is based (e.g. 'Germany', 'Spain', 'France')",
  "sector": "one of: Healthcare, Finance, Technology, Telecom, Retail, Education, Government, Legal, Energy, Other",
  "data_types": ["list of data types processed, e.g. 'financial', 'health', 'biometric', 'contact', 'identification', 'behavioral', 'location'"],
  "legal_bases": ["list of GDPR legal bases used, e.g. 'consent', 'legitimate_interest', 'contract', 'legal_obligation', 'vital_interest', 'public_interest'"],
  "transfers": [{{"destination": "country", "mechanism": "SCCs/BCRs/adequacy/other"}}],
  "special_categories": true/false
}}

If a field cannot be determined from the text, use null or empty list.
For jurisdiction, look for: registered office address, DPA mentioned, governing law clause, country in contact details.
For sector, infer from the type of services described.

PRIVACY POLICY TEXT:
{policy_text}

Return ONLY the JSON object, no explanation."""


def _parse_llm_response(
    content: str, company_url: str, raw_snippet: str
) -> OrgProfile | None:
    """Parse LLM JSON response into OrgProfile."""
    # Extract JSON from response (handle markdown code blocks)
    json_match = re.search(r"\{[\s\S]*\}", content)
    if not json_match:
        log.error("No JSON found in LLM response")
        return None

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        log.error("Invalid JSON from LLM: %s", e)
        return None

    return OrgProfile(
        company_url=company_url,
        company_name=data.get("company_name"),
        jurisdiction=data.get("jurisdiction"),
        sector=data.get("sector"),
        data_types=data.get("data_types", []),
        legal_bases=data.get("legal_bases", []),
        transfers=data.get("transfers", []),
        special_categories=data.get("special_categories", False),
        raw_snippet=raw_snippet,
    )


def profile_to_dict(profile: OrgProfile) -> dict:
    """Convert OrgProfile to dict for storage in user_memory JSONB."""
    return asdict(profile)


# ── CLI test ──
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python profile_scraper.py <url>")
        print("Example: python profile_scraper.py https://stripe.com")
        sys.exit(1)

    url = sys.argv[1]
    print(f"Scraping privacy policy from: {url}")

    text = scrape_privacy_policy(url)
    if not text:
        print("ERROR: Could not find privacy policy")
        sys.exit(1)

    print(f"Found policy text ({len(text)} chars)")
    print(f"First 200 chars: {text[:200]}...")
    print()

    print("Extracting profile with LLM...")
    profile = extract_org_profile(text, url)
    if profile:
        print(f"\nRESULT:")
        print(f"  Company:      {profile.company_name}")
        print(f"  Jurisdiction: {profile.jurisdiction}")
        print(f"  Sector:       {profile.sector}")
        print(f"  Data types:   {profile.data_types}")
        print(f"  Legal bases:  {profile.legal_bases}")
        print(f"  Transfers:    {profile.transfers}")
        print(f"  Special cats: {profile.special_categories}")
    else:
        print("ERROR: Profile extraction failed")
