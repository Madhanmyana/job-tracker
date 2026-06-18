"""
scraper.py
==========
Stream B — Isolated web-scraping ingestion using ``cloudscraper``.

Design principles
-----------------
- Each scrape target runs inside its own ``try/except`` block.  A failure for
  one target is logged and skipped; it never propagates to the caller or
  blocks the Gmail stream.
- No credentials are used here; all targets must be publicly accessible.
- The returned dicts share the same schema as those from ``gmail_imap.py``
  so that ``main.py`` can merge both streams without branching.

Adding targets
--------------
Add entries to ``config.SCRAPE_TARGETS`` — no changes to this file are needed.
Each entry is a dict::

    {
        "name": "Internshala Cloud Jobs",
        "url": "https://internshala.com/jobs/cloud-computing-jobs/",
    }
"""

import logging

import cloudscraper
from bs4 import BeautifulSoup

from config import SCRAPE_TARGETS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_jobs_from_page(name: str, html: str, source_url: str) -> list[dict]:
    """
    Generic extraction: pull all <a> tags that look like job listings or
    'Apply' links from *html*.

    This is a best-effort heuristic suitable for most job-board pages.
    For highly dynamic SPA pages (React/Vue rendered) that serve empty HTML
    to scrapers, results may be sparse — add a custom parser function to
    ``config.SCRAPE_TARGETS`` as needed.
    """
    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text(separator=" ", strip=True)[:4000]

    job_links: list[str] = []
    keywords = ("apply", "job", "career", "internship", "position", "role",
                 "opening", "hiring", "opportunity")

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        text: str = tag.get_text(strip=True).lower()

        if not href.startswith("http"):
            continue
        if any(kw in href.lower() or kw in text for kw in keywords):
            if href not in job_links:
                job_links.append(href)

    if not job_links:
        logger.debug("[%s] No job links found on page.", name)
        return []

    # Build one synthetic job dict per link found (mirrors Gmail stream schema)
    jobs: list[dict] = []
    for link in job_links:
        jobs.append(
            {
                "title": f"[Scraped] {name}",
                "text": body_text,
                "apply_url": link,
                "all_urls": job_links,
                "source": name,
            }
        )

    logger.info("[%s] Extracted %d potential job link(s).", name, len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_all() -> list[dict]:
    """
    Scrape every target in ``config.SCRAPE_TARGETS`` and return a combined
    list of raw job dicts.

    Returns an empty list if ``SCRAPE_TARGETS`` is empty or if all targets
    fail.  Individual failures are logged as warnings and skipped.
    """
    if not SCRAPE_TARGETS:
        logger.info("No scrape targets configured; Stream B skipped.")
        return []

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    all_jobs: list[dict] = []

    for target in SCRAPE_TARGETS:
        name: str = target.get("name", target.get("url", "unknown"))
        url: str = target.get("url", "")

        if not url:
            logger.warning("Scrape target '%s' has no URL; skipping.", name)
            continue

        try:
            logger.info("[%s] Scraping %s …", name, url)
            response = scraper.get(url, timeout=20)
            response.raise_for_status()
            jobs = _extract_jobs_from_page(name, response.text, url)
            all_jobs.extend(jobs)

        except Exception as exc:  # noqa: BLE001
            # Isolated failure — log and continue to next target
            logger.warning(
                "[%s] Scraping failed (%s: %s); skipping this target.",
                name,
                type(exc).__name__,
                exc,
            )

    logger.info("Stream B complete: %d job(s) collected from scraping.", len(all_jobs))
    return all_jobs
