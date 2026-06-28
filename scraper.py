"""
scraper.py
==========
Stream B — Multi-keyword, paginated aggregate job-board scraper.

Design principles
-----------------
- Targets two aggregate fresher job boards (IT Jobs & Internship Jobs on
  freshersjobs24.com) instead of static corporate career pages.
- Iterates over a configurable list of search keywords and paginates up to
  5 pages per keyword × board combination.
- Extracts structured job dicts matching the internal pipeline schema so that
  ``ai_filter.py`` can perform semantic evaluation without transformation.
- Deduplicates results by URL before returning.
- Injects a mandatory ``time.sleep(2)`` between page requests to stay under
  scraping detection thresholds.

Safety checks
-------------
Each pagination loop breaks early if:
  - The HTTP response returns a non-200 status code.
  - Zero new job elements are found on the page.
  - The page content is identical to the previous page (end-of-results signal).

Adding targets
--------------
Add entries to ``SCRAPE_BOARDS`` below.  Each entry is a dict::

    {
        "name": "Board Display Name",
        "base_url": "https://example.com/jobs",
    }
"""

import logging
import time
from urllib.parse import urlencode

import cloudscraper
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — Aggregate board targets
# ---------------------------------------------------------------------------

# Resolved from:
#   IT Jobs Board:         https://tinyurl.com/2s722xa4
#   Internship Jobs Board: https://tinyurl.com/mtshpdap
SCRAPE_BOARDS: list[dict] = [
    {
        "name": "FreshersJobs24 — IT Jobs",
        "base_url": "http://freshersjobs24.com/category/it-jobs/",
        "locations": ["hyderabad"],
    },
    {
        "name": "FreshersJobs24 — Internship Jobs",
        "base_url": "http://freshersjobs24.com/category/internship-jobs/",
        "locations": ["hyderabad", "remote"],
    },
]

SEARCH_KEYWORDS: list[str] = [
    "python developer",
    "python backend developer",
    "backend internship",
    "remote python developer",
    "backend developer intern",
]

# Fallback if a board entry omits the "locations" key.
DEFAULT_LOCATIONS: list[str] = ["hyderabad"]

# Maximum pages to crawl per keyword × board combination.
_MAX_PAGES: int = 5

# Seconds to sleep between consecutive HTTP requests.
_REQUEST_DELAY: float = 2.0

# Query parameter key names — swap these if a board uses different keys.
_PARAM_KEY_KEYWORD: str = "keyword"
_PARAM_KEY_LOCATION: str = "location"
_PARAM_KEY_PAGE: str = "page"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_search_url(
    base_url: str,
    keyword: str,
    location: str,
    page: int,
    *,
    param_keyword: str = _PARAM_KEY_KEYWORD,
    param_location: str = _PARAM_KEY_LOCATION,
    param_page: str = _PARAM_KEY_PAGE,
) -> str:
    """
    Construct a dynamic search URL by appending query parameters.

    Parameters
    ----------
    base_url : str
        The board's base URL (e.g. ``http://freshersjobs24.com/category/it-jobs/``).
    keyword : str
        Search keyword (e.g. ``"python developer"``).
    location : str
        Target location (e.g. ``"hyderabad"``).
    page : int
        Page number (1-indexed).
    param_keyword : str
        Query parameter key for the keyword (default ``"keyword"``).
    param_location : str
        Query parameter key for the location (default ``"location"``).
    param_page : str
        Query parameter key for the page number (default ``"page"``).

    Returns
    -------
    str
        Fully-formed URL with encoded query string.
    """
    query_params = {
        param_keyword: keyword,
        param_location: location,
        param_page: str(page),
    }
    separator = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('/')}/{separator}{urlencode(query_params)}"


def _extract_jobs_from_page(html: str, board_name: str) -> list[dict]:
    """
    Parse job listings from an HTML page and return structured dicts.

    The extraction targets ``<article>`` elements (WordPress archive standard)
    and falls back to ``<a>`` link heuristics if no articles are found.

    Each returned dict follows the pipeline schema expected by ``dedup.py``
    and ``ai_filter.py``::

        {
            "title":       str,   # clean job title text
            "company":     str,   # board name (company not always available)
            "url":         str,   # canonical job detail URL
            "description": str,   # clean description text (body excerpt)
            "apply_url":   str,   # same as url — used by dedup.filter_new
            "text":        str,   # alias for description — used by ai_filter
            "source":      str,   # board identifier for logging
        }
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []

    # ── Strategy 1: WordPress <article> elements ──────────────────────────
    articles = soup.find_all("article")
    if articles:
        for article in articles:
            # Title: look for heading tag with an <a> inside
            title_tag = None
            for heading_level in ("h2", "h3", "h1", "h4"):
                title_tag = article.find(heading_level)
                if title_tag:
                    break

            if not title_tag:
                continue

            link_tag = title_tag.find("a", href=True) if title_tag else None
            title_text = title_tag.get_text(strip=True) if title_tag else ""
            job_url = link_tag["href"].strip() if link_tag else ""

            if not job_url or not job_url.startswith("http"):
                continue

            # Description: grab the entry content / excerpt
            desc_tag = article.find(
                "div", class_=lambda c: c and ("entry" in c or "excerpt" in c or "content" in c)
            )
            if not desc_tag:
                desc_tag = article.find("p")
            description = desc_tag.get_text(separator=" ", strip=True) if desc_tag else ""

            jobs.append({
                "title": title_text,
                "company": board_name,
                "url": job_url,
                "description": description[:4000],
                "apply_url": job_url,
                "text": f"{title_text}. {description[:4000]}",
                "source": board_name,
            })

        return jobs

    # ── Strategy 2: Fallback — scan all <a> links with job-like keywords ──
    keywords = (
        "apply", "job", "career", "internship", "position", "role",
        "opening", "hiring", "opportunity", "fresher", "developer",
        "engineer", "python", "backend",
    )

    seen_hrefs: set[str] = set()
    body_text = soup.get_text(separator=" ", strip=True)[:4000]

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        text: str = tag.get_text(strip=True)

        if not href.startswith("http"):
            continue
        if href in seen_hrefs:
            continue
        if not any(kw in href.lower() or kw in text.lower() for kw in keywords):
            continue

        seen_hrefs.add(href)
        jobs.append({
            "title": text or f"[Scraped] {board_name}",
            "company": board_name,
            "url": href,
            "description": body_text,
            "apply_url": href,
            "text": f"{text}. {body_text}",
            "source": board_name,
        })

    return jobs


def _deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    """Remove duplicate jobs based on their ``url`` field."""
    seen_urls: set[str] = set()
    unique: list[dict] = []

    for job in jobs:
        url = job.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(job)

    removed = len(jobs) - len(unique)
    if removed:
        logger.info("Deduplication removed %d duplicate(s); %d unique job(s) remain.", removed, len(unique))

    return unique


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_all() -> list[dict]:
    """
    Scrape every configured board across all keywords and pages.

    Returns a deduplicated flat list of job dicts matching the internal
    pipeline schema.  Returns an empty list if all requests fail.

    The output dicts contain keys expected by downstream modules:
      - ``apply_url`` (str) — used by ``dedup.filter_new`` for hash-based dedup
      - ``title`` (str)     — used by ``ai_filter._build_prompt``
      - ``text`` (str)      — used by ``ai_filter._build_prompt`` (body snippet)
      - ``source`` (str)    — used by ``ai_filter._keyword_fallback``
    """
    if not SCRAPE_BOARDS:
        logger.info("No scrape boards configured; Stream B skipped.")
        return []

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    all_jobs: list[dict] = []
    total_combinations = sum(
        len(b.get("locations", DEFAULT_LOCATIONS)) for b in SCRAPE_BOARDS
    ) * len(SEARCH_KEYWORDS)
    combination_idx = 0

    for keyword in SEARCH_KEYWORDS:
        for board in SCRAPE_BOARDS:
            board_name: str = board.get("name", "Unknown Board")
            base_url: str = board.get("base_url", "")
            locations: list[str] = board.get("locations", DEFAULT_LOCATIONS)

            if not base_url:
                logger.warning("[%s] Board has no base_url; skipping.", board_name)
                continue

            for location in locations:
                combination_idx += 1

                logger.info(
                    "━━━ [%d/%d] Keyword: '%s' | Location: %s | Board: %s ━━━",
                    combination_idx, total_combinations, keyword, location, board_name,
                )

                prev_content: str = ""

                for page in range(1, _MAX_PAGES + 1):
                    target_url = _build_search_url(
                        base_url, keyword, location, page,
                    )

                    logger.info(
                        "  📄 Page %d/%d — %s",
                        page, _MAX_PAGES, target_url,
                    )

                    try:
                        response = scraper.get(
                            target_url,
                            timeout=15,
                            allow_redirects=True,
                        )

                        # ── Safety check 1: non-200 status ────────────────
                        if response.status_code != 200:
                            logger.warning(
                                "  ⚠️  Page %d returned HTTP %d; stopping pagination for this combo.",
                                page, response.status_code,
                            )
                            break

                        page_html: str = response.text

                        # ── Safety check 3: duplicate content detection ───
                        if page_html == prev_content:
                            logger.info(
                                "  🔁 Page %d content identical to previous page; stopping pagination.",
                                page,
                            )
                            break

                        prev_content = page_html

                        # ── Extract jobs ──────────────────────────────────
                        page_jobs = _extract_jobs_from_page(page_html, board_name)

                        # ── Safety check 2: zero results ─────────────────
                        if not page_jobs:
                            logger.info(
                                "  🚫 Page %d yielded 0 job elements; stopping pagination.",
                                page,
                            )
                            break

                        logger.info(
                            "  ✅ Page %d: extracted %d job(s).",
                            page, len(page_jobs),
                        )
                        all_jobs.extend(page_jobs)

                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "  ❌ Page %d request failed (%s: %s); stopping pagination.",
                            page, type(exc).__name__, exc,
                        )
                        break

                    # ── Rate limiting ─────────────────────────────────────
                    if page < _MAX_PAGES:
                        time.sleep(_REQUEST_DELAY)

    # ── Final deduplication across all boards and keywords ─────────────────
    unique_jobs = _deduplicate_jobs(all_jobs)

    logger.info(
        "Stream B complete: %d total job(s) scraped → %d unique job(s) after dedup.",
        len(all_jobs), len(unique_jobs),
    )
    return unique_jobs
