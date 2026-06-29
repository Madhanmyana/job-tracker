"""
main.py
=======
Pipeline orchestrator for the automated job-filtering system.

Execution order
---------------
1.  Load config & seen hash-set from ``seen_jobs.json``
2.  Stream A  — Gmail IMAP: fetch unread jobs from ``Daily-Jobs`` only
3.  Stream B  — Scraping:   fetch jobs from configured targets (isolated try/except)
4.  Stream C  — YouTube RSS: extract job links from OnlineStudy4u (isolated)
5.  Merge all streams and deduplicate against the seen set
6.  Abort gracefully if there are no new jobs
7.  AI filter — Groq (llama-3.3-70b-versatile): score and tier-classify
    non-YouTube jobs.  YouTube links bypass AI and go directly to email.
8.  Abort gracefully if no jobs pass the tier threshold AND there are no
    YouTube links
9.  Build HTML report and send via SMTP (self → self)
10. ONLY on SMTP success:
      a. Mark Gmail alert emails as READ (via IMAP)
      b. Append new hashes to seen set and overwrite ``seen_jobs.json``

Transactional guarantee
-----------------------
Steps 10a and 10b execute **only** after step 9 returns ``True``.  If the
SMTP send fails at any point:
  - No Gmail messages are marked as read
  - ``seen_jobs.json`` is not updated
  - The next run will re-process the same emails and retry
"""

import logging
import sys

# ── Configure logging before any imports that use it ──────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("main")

# ── Module imports (config validates env-vars on import; fails fast) ───────
from config import (          # noqa: E402
    GMAIL_FOLDER,
    SEEN_JOBS_FILE,
    TIER_A,
    TIER_B,
)
from dedup import filter_new, load_seen, save_seen   # noqa: E402
from gmail_imap import fetch_unread_jobs, mark_as_read  # noqa: E402
from scraper import scrape_all                          # noqa: E402
from ai_filter import evaluate_jobs                     # noqa: E402
from email_report import build_html, send_report        # noqa: E402
from youtube_stream import scrape_youtube_links          # noqa: E402


def main() -> None:
    logger.info("=" * 60)
    logger.info("Job-Tracker Pipeline START")
    logger.info("=" * 60)

    # ── Step 1: Load deduplication state ─────────────────────────────────
    seen: set[str] = load_seen()
    logger.info("Loaded %d previously seen URL hash(es).", len(seen))

    # ── Step 2: Stream A — Gmail IMAP ────────────────────────────────────
    logger.info("--- Stream A: Gmail IMAP ingestion ---")
    gmail_jobs: list[dict] = []
    email_uids: list[bytes] = []
    try:
        gmail_jobs, email_uids = fetch_unread_jobs()
    except Exception as exc:   # noqa: BLE001
        # A complete Gmail failure is treated as fatal because Stream A is
        # the primary data source.
        logger.error("Stream A failed with an unexpected error: %s", exc)
        sys.exit(1)

    # ── Step 3: Stream B — Scraping (isolated) ───────────────────────────
    logger.info("--- Stream B: Web scraping ---")
    scraped_jobs: list[dict] = []
    try:
        scraped_jobs = scrape_all()
    except Exception as exc:   # noqa: BLE001
        # Stream B failure is non-fatal; pipeline continues with Gmail jobs.
        logger.warning("Stream B raised an unhandled exception: %s. Continuing.", exc)

    # ── Step 4: Stream C — YouTube RSS (isolated) ────────────────────────
    logger.info("--- Stream C: YouTube RSS (OnlineStudy4u) ---")
    youtube_jobs_raw: list[dict] = []
    try:
        youtube_jobs_raw = scrape_youtube_links()
    except Exception as exc:   # noqa: BLE001
        # Stream C failure is non-fatal; pipeline continues without YT links.
        logger.warning("Stream C raised an unhandled exception: %s. Continuing.", exc)

    # ── Step 5: Merge & deduplicate ───────────────────────────────────────
    all_raw: list[dict] = gmail_jobs + scraped_jobs + youtube_jobs_raw
    logger.info(
        "Combined: %d Gmail + %d scraped + %d YouTube = %d total.",
        len(gmail_jobs), len(scraped_jobs), len(youtube_jobs_raw), len(all_raw),
    )

    new_jobs, new_hashes = filter_new(all_raw, seen)

    # Separate YouTube links from jobs that need AI evaluation.
    # YouTube links bypass the Groq LLM entirely.
    youtube_links: list[dict] = [j for j in new_jobs if j.get("source") == "youtube"]
    ai_candidate_jobs: list[dict] = [j for j in new_jobs if j.get("source") != "youtube"]

    if not new_jobs:
        logger.info("No new jobs after deduplication. Pipeline complete — nothing to send.")
        # Still mark processed emails as read even if all were duplicates,
        # so the inbox stays clean.
        if email_uids:
            logger.info("Marking %d already-seen email(s) as read to keep inbox clean.", len(email_uids))
            mark_as_read(email_uids)
        return

    logger.info(
        "%d new item(s): %d for AI evaluation, %d YouTube link(s) (bypass AI).",
        len(new_jobs), len(ai_candidate_jobs), len(youtube_links),
    )

    # ── Step 6 (implicit) — no new jobs was handled above ─────────────────

    # ── Step 7: AI tier filtering (non-YouTube jobs only) ─────────────────
    filtered_jobs = []
    if ai_candidate_jobs:
        logger.info("--- AI Evaluation (Groq — llama-3.3-70b-versatile) ---")
        try:
            filtered_jobs = evaluate_jobs(ai_candidate_jobs)
        except ValueError as exc:
            logger.error("AI evaluation failed: %s", exc)
            # If we still have YouTube links, continue with those alone.
            if not youtube_links:
                logger.error("Pipeline aborted. No emails sent; state unchanged.")
                sys.exit(1)
            logger.warning("Continuing with %d YouTube link(s) only.", len(youtube_links))

    if not filtered_jobs and not youtube_links:
        logger.info(
            "No jobs passed the tier filter and no YouTube links today. "
            "Marking emails as read and updating seen set."
        )
        # Even though no jobs are worth reporting, update state to avoid
        # re-processing the same emails tomorrow.
        mark_as_read(email_uids)
        seen.update(new_hashes)
        save_seen(seen)
        return

    if filtered_jobs:
        logger.info(
            "%d job(s) passed AI filter: %d Tier A, %d Tier B.",
            len(filtered_jobs),
            sum(1 for j in filtered_jobs if j.match_tier == TIER_A),
            sum(1 for j in filtered_jobs if j.match_tier == TIER_B),
        )
    if youtube_links:
        logger.info("%d YouTube link(s) will be included directly.", len(youtube_links))

    # ── Step 8: Build and send the HTML report ────────────────────────────
    logger.info("--- Email Delivery (SMTP) ---")
    total_items = len(filtered_jobs) + len(youtube_links)
    html_body = build_html(filtered_jobs, youtube_links=youtube_links)
    smtp_success: bool = send_report(html_body, job_count=total_items)

    # ── Step 9 / 10: Transactional commit ────────────────────────────────
    if smtp_success:
        logger.info("--- Transactional Commit ---")

        # 10a. Mark Gmail alert emails as read (IMAP)
        mark_as_read(email_uids)

        # 10b. Persist new hashes to seen_jobs.json
        seen.update(new_hashes)
        save_seen(seen)

        logger.info(
            "Pipeline complete. %d new hash(es) added; total seen: %d.",
            len(new_hashes), len(seen),
        )
    else:
        logger.error(
            "SMTP send FAILED. "
            "Gmail messages NOT marked as read. "
            "seen_jobs.json NOT updated. "
            "The next run will retry these emails."
        )
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Job-Tracker Pipeline END")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
