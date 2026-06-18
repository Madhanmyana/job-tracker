"""
config.py
=========
Centralised configuration and environment-variable loading.

All secrets are injected exclusively via environment variables (GitHub Secrets
in CI, or a local .env / shell export for development).  No secret is ever
hard-coded here.
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


GEMINI_API_KEY: str = _require_env("GEMINI_API_KEY")
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
# Gemini AI
# ---------------------------------------------------------------------------

GEMINI_MODEL: str = "gemini-1.5-flash"

GEMINI_SYSTEM_INSTRUCTION: str = (
    "You are an expert IT technical recruiter. Evaluate the provided job and internship "
    "descriptions against an entry-level 'Fresher' (0-1 years experience) Backend Engineer profile. "
    "The target roles are: Backend Engineer, Python Developer, Python Backend Developer, and related Internships. "
    "The core skill set includes: Python, FastAPI, REST APIs, SQL, JWT, and Authentication. "
    "Classification Rules: "
    "- 'Tier_A_Strong': Explicitly requires Python backend skills (FastAPI/REST) AND is clearly for a fresher, intern, or entry-level candidate (0-1 years). "
    "- 'Tier_B_Fuzzy': General software roles requiring Python/SQL, or entry-level roles where backend is only part of the stack. Also use this for roles asking for 1-2 years of experience where a strong fresher might still have a chance. "
    "- 'Tier_C_None': Strictly discard roles requiring 3+ years of experience, purely frontend roles, completely different stacks (e.g., exclusively Java/Spring Boot or Node.js), or non-engineering positions. "
    "Classify roles strictly into 'Tier_A_Strong', 'Tier_B_Fuzzy', or 'Tier_C_None'. "
    "Prioritize accurate experience-level matching over raw keyword volume."
)

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
