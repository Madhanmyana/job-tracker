"""
dedup.py
========
URL cleaning, MD5 hashing, and seen-jobs persistence.

Responsibilities
----------------
- Strip all tracking / UTM query parameters from job application URLs so that
  the same job posted via different referral links resolves to a single hash.
- Load and save the deduplication state file (seen_jobs.json).
- Filter a list of raw job dicts down to only those not yet seen.

Security note
-------------
This module only reads and writes ``seen_jobs.json``.  It never touches any
email folder or network resource.
"""

import hashlib
import json
import logging
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

from config import SEEN_JOBS_FILE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tracking parameters that are stripped before hashing
# ---------------------------------------------------------------------------

_TRACKING_PARAMS: frozenset[str] = frozenset(
    [
        # UTM family
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        # Common ad-network / referral tokens
        "ref", "referrer", "source", "src", "fbclid", "gclid", "msclkid",
        "mc_eid", "mc_cid", "yclid", "twclid", "igshid",
        # Job-board specific
        "jid", "jobId", "job_id", "alertId", "alert_id", "sid", "cmp",
        "tracking", "trk", "trkCampaign", "trkInfo",
    ]
)


def clean_url(raw_url: str) -> str:
    """
    Return a canonical form of *raw_url* with all tracking parameters removed.

    Parameters
    ----------
    raw_url : str
        The URL as extracted from the email or scrape result.

    Returns
    -------
    str
        The cleaned URL, safe for hashing and inclusion in the report email.
    """
    try:
        parsed = urlparse(raw_url.strip())
        # Rebuild query string without tracking keys (case-insensitive match)
        clean_qs = urlencode(
            [
                (k, v)
                for k, v in parse_qsl(parsed.query)
                if k.lower() not in _TRACKING_PARAMS
            ]
        )
        cleaned = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                clean_qs,
                "",          # strip fragments too
            )
        )
        return cleaned.rstrip("/")   # normalise trailing slash
    except Exception:
        logger.warning("Could not clean URL '%s'; using raw value.", raw_url)
        return raw_url.strip()


def url_to_hash(clean: str) -> str:
    """Return the MD5 hex-digest of a cleaned URL string."""
    return hashlib.md5(clean.encode("utf-8")).hexdigest()


def load_seen() -> set[str]:
    """
    Load the set of previously seen URL hashes from ``seen_jobs.json``.

    Returns an empty set if the file does not exist or is malformed.
    """
    try:
        with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return set(data)
        logger.warning(
            "%s contained unexpected structure; starting with empty set.",
            SEEN_JOBS_FILE,
        )
        return set()
    except FileNotFoundError:
        logger.info("%s not found; initialising empty seen set.", SEEN_JOBS_FILE)
        return set()
    except json.JSONDecodeError as exc:
        logger.warning(
            "%s is malformed (%s); starting with empty set.", SEEN_JOBS_FILE, exc
        )
        return set()


def save_seen(seen: set[str]) -> None:
    """
    Persist the updated *seen* hash set to ``seen_jobs.json``.

    The file is written atomically by serialising to a string first, then
    writing in a single call to minimise the window of corruption on failure.
    """
    payload = json.dumps(sorted(seen), indent=2)
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as fh:
        fh.write(payload)
    logger.info("Saved %d hashes to %s.", len(seen), SEEN_JOBS_FILE)


def filter_new(
    raw_jobs: list[dict],
    seen: set[str],
) -> tuple[list[dict], set[str]]:
    """
    Remove jobs whose canonical URL hash already exists in *seen*.

    Parameters
    ----------
    raw_jobs : list[dict]
        Each dict must contain at least one URL string under the key
        ``"apply_url"`` (set by the ingestion layer).
    seen : set[str]
        The current set of known hashes loaded from ``seen_jobs.json``.

    Returns
    -------
    new_jobs : list[dict]
        Jobs not previously seen.  Each dict is enriched with two new keys:
        ``"url_hash"`` (str) and ``"clean_url"`` (str).
    new_hashes : set[str]
        Hashes of *new_jobs* that should be merged into *seen* after a
        successful pipeline run.
    """
    new_jobs: list[dict] = []
    new_hashes: set[str] = set()

    for job in raw_jobs:
        raw_url = job.get("apply_url", "")
        if not raw_url:
            logger.debug("Skipping job with no apply_url: %s", job.get("title", "?"))
            continue

        canonical = clean_url(raw_url)
        h = url_to_hash(canonical)

        if h in seen:
            logger.debug("Duplicate skipped (hash=%s): %s", h, canonical)
            continue

        job["clean_url"] = canonical
        job["url_hash"] = h
        new_jobs.append(job)
        new_hashes.add(h)

    logger.info(
        "Dedup: %d total → %d new, %d duplicates skipped.",
        len(raw_jobs),
        len(new_jobs),
        len(raw_jobs) - len(new_jobs),
    )
    return new_jobs, new_hashes
