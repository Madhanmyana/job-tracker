"""
main.py
=======
Pipeline orchestrator for the automated job-filtering system.

Execution order
---------------
1.  Load config & seen hash-set from ``seen_jobs.json``
2.  Stream A  — Gmail IMAP: fetch unread jobs from ``Daily-Jobs`` only
3.  Stream B  — Scraping:   fetch jobs from configured targets (isolated try/except)
4.  Merge both streams and deduplicate against the seen set
5.  Abort gracefully if there are no new jobs
6.  AI filter — Gemini 2.0 Flash: score and tier-classify new jobs
7.  Abort gracefully if no jobs pass the tier threshold
8.  Build HTML report and send via SMTP (self → self)
9.  ONLY on SMTP success:
      a. Mark Gmail alert emails as READ (via IMAP)
      b. Append new hashes to seen set and overwrite ``seen_jobs.json``

Transactional guarantee
-----------------------
Steps 9a and 9b execute **only** after step 8 returns ``True``.  If the SMTP
send fails at any point:
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

    # ── Step 4: Merge & deduplicate ───────────────────────────────────────
    all_raw: list[dict] = gmail_jobs + scraped_jobs
    logger.info(
        "Combined: %d job(s) from Gmail + %d from scraping = %d total.",
        len(gmail_jobs), len(scraped_jobs), len(all_raw),
    )

    new_jobs, new_hashes = filter_new(all_raw, seen)

    if not new_jobs:
        logger.info("No new jobs after deduplication. Pipeline complete — nothing to send.")
        # Still mark processed emails as read even if all were duplicates,
        # so the inbox stays clean.
        if email_uids:
            logger.info("Marking %d already-seen email(s) as read to keep inbox clean.", len(email_uids))
            mark_as_read(email_uids)
        return

    logger.info("%d new job(s) will be evaluated by AI.", len(new_jobs))

    # ── Step 5 (implicit) — no new jobs was handled above ─────────────────

    # ── Step 6: AI tier filtering ─────────────────────────────────────────
    logger.info("--- AI Evaluation (Gemini 2.0 Flash) ---")
    try:
        filtered_jobs = evaluate_jobs(new_jobs)
    except ValueError as exc:
        logger.error("AI evaluation failed: %s", exc)
        logger.error("Pipeline aborted. No emails sent; state unchanged.")
        sys.exit(1)

    if not filtered_jobs:
        logger.info(
            "No jobs passed the tier filter today. "
            "Marking emails as read and updating seen set."
        )
        # Even though no jobs are worth reporting, update state to avoid
        # re-processing the same emails tomorrow.
        mark_as_read(email_uids)
        seen.update(new_hashes)
        save_seen(seen)
        return

    logger.info(
        "%d job(s) passed: %d Tier A, %d Tier B.",
        len(filtered_jobs),
        sum(1 for j in filtered_jobs if j.match_tier == TIER_A),
        sum(1 for j in filtered_jobs if j.match_tier == TIER_B),
    )

    # ── Step 7: Build and send the HTML report ────────────────────────────
    logger.info("--- Email Delivery (SMTP) ---")
    html_body = build_html(filtered_jobs)
    smtp_success: bool = send_report(html_body, job_count=len(filtered_jobs))

    # ── Step 8 / 9: Transactional commit ─────────────────────────────────
    if smtp_success:
        logger.info("--- Transactional Commit ---")

        # 9a. Mark Gmail alert emails as read (IMAP)
        mark_as_read(email_uids)

        # 9b. Persist new hashes to seen_jobs.json
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
