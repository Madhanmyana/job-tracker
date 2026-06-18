"""
config.py
=========
Centralised configuration and environment-variable loading.

All secrets are injected exclusively via environment variables (GitHub Secrets
in CI, or a local .env / shell export for development).  No secret is ever
hard-coded here.

Migration note (2026-06-18)
---------------------------
Switched AI backend from Google Gemini → Groq (llama-3.3-70b-versatile) to
bypass free-tier 429 rate limits on the Gemini API.
"""

import os
import sys

# ---------------------------------------------------------------------------
# Secret validation — fail fast if anything is missing
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Return the value of an env-var or abort with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"[FATAL] Required environment variable '{name}' is not set. "
            "Ensure it is defined in GitHub Secrets or your local shell."
        )
    return value


GROQ_API_KEY: str = _require_env("GROQ_API_KEY")
GMAIL_USER_EMAIL: str = _require_env("GMAIL_USER_EMAIL")
GMAIL_APP_PASSWORD: str = _require_env("GMAIL_APP_PASSWORD")

# ---------------------------------------------------------------------------
# IMAP (Gmail ingestion)
# ---------------------------------------------------------------------------

IMAP_HOST: str = "imap.gmail.com"
IMAP_PORT: int = 993

# The ONLY folder the script is allowed to open.
GMAIL_FOLDER: str = "Daily-Jobs"

# ---------------------------------------------------------------------------
# SMTP (report delivery)
# ---------------------------------------------------------------------------

SMTP_HOST: str = "smtp.gmail.com"
SMTP_PORT: int = 465          # SSL

# ---------------------------------------------------------------------------
# Groq AI
# ---------------------------------------------------------------------------

# Model used for all AI scoring and tier classification.
GROQ_MODEL: str = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Tier filtering thresholds
# ---------------------------------------------------------------------------

TIER_A: str = "Tier_A_Strong"
TIER_B: str = "Tier_B_Fuzzy"
TIER_C: str = "Tier_C_None"

MIN_TIER_B_SCORE: int = 70   # Tier_B jobs below this score are discarded

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SEEN_JOBS_FILE: str = "seen_jobs.json"

# ---------------------------------------------------------------------------
# Stream B — Scraping targets
# ---------------------------------------------------------------------------
# Each entry is a dict with keys:
#   name (str)  : human-readable label used in logs
#   url  (str)  : full URL to scrape
#
# Add your Internshala / custom career-site URLs here.
# The scraping block in scraper.py is fully isolated in a try/except, so a
# failure for one target never blocks the rest of the pipeline.
# ---------------------------------------------------------------------------

SCRAPE_TARGETS: list[dict] = [
    {
        "name": "Infosys Careers",
        "url": "https://sjobs.brassring.com",
    },
    {
        "name": "Wipro Careers",
        "url": "https://wiproaerospace.com/careers/",
    },
    {
        "name": "TCS Careers",
        "url": "https://www.tcs.com/careers/india",
    },
    {
        "name": "Deloitte Careers",
        "url": "https://www.deloitte.com/us/en/careers/careers.html",
    },
    {
        "name": "Accenture Careers",
        "url": "https://www.accenture.com/in-en/careers",
    },
    {
        "name": "Capgemini Careers",
        "url": "https://www.capgemini.com/careers/join-capgemini/job-search/",
    },
    {
        "name": "Cognizant Careers",
        "url": "https://careers.cognizant.com/india-en/",
    },
]
