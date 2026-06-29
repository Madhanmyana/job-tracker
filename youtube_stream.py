"""
youtube_stream.py
=================
Stream C — YouTube RSS link extraction for the OnlineStudy4u channel.

Fetches the **single most recent video** from the channel's public Atom feed,
strips out social-media spam links via a strict blacklist, and returns the
remaining job / portal URLs as pipeline-compatible dictionaries.

Each returned dict also carries a ``video_url`` key so the email renderer
can feature the source video prominently.

This stream is **non-fatal**: if the feed is unreachable or unparseable the
caller receives an empty list and the pipeline continues with Streams A / B.
"""

import logging
import re
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel configuration
# ---------------------------------------------------------------------------

_CHANNEL_ID: str = "UC512aL5wp8icOicjwwWtOyg"  # OnlineStudy4u
_FEED_URL: str = (
    f"https://www.youtube.com/feeds/videos.xml?channel_id={_CHANNEL_ID}"
)

# ---------------------------------------------------------------------------
# XML namespace map (YouTube Atom feeds use the media:* namespace)
# ---------------------------------------------------------------------------

_NS: dict[str, str] = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

# ---------------------------------------------------------------------------
# URL extraction & strict blacklist filtering
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"(https?://[^\s]+)")

# Social-media / messaging domains to always drop.
_BLACKLISTED_DOMAINS: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "t.me",
    "telegram.me",
    "whatsapp.com",
)


def _is_blacklisted(url: str) -> bool:
    """Return ``True`` if *url* contains any blacklisted domain."""
    lower = url.lower()
    return any(domain in lower for domain in _BLACKLISTED_DOMAINS)


def _extract_job_urls(text: str) -> list[str]:
    """
    Extract all URLs from *text*, drop anything matching the social-media
    blacklist, and return the remaining links.
    """
    if not text:
        return []
    raw_urls = _URL_RE.findall(text)
    return [u for u in raw_urls if not _is_blacklisted(u)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrape_youtube_links() -> list[dict]:
    """
    Fetch the YouTube RSS feed for OnlineStudy4u and return job-like dicts
    from the **single most recent video** only.

    Each returned dict has the shape expected by ``dedup.filter_new``::

        {
            "title":      "<video title>",
            "url":         <extracted job link>,
            "apply_url":   <same — used by dedup.filter_new>,
            "source":     "youtube",
            "video_url":   <YouTube watch link for the source video>,
        }

    Returns an empty list on any network or parsing failure.
    """
    logger.info(
        "Fetching YouTube RSS feed for channel %s …", _CHANNEL_ID
    )

    try:
        resp = requests.get(_FEED_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch YouTube RSS feed: %s", exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        logger.warning("Failed to parse YouTube RSS XML: %s", exc)
        return []

    entries = root.findall("atom:entry", _NS)
    if not entries:
        logger.info("YouTube feed returned 0 entries.")
        return []

    # ── Process only the latest (first) video ─────────────────────────────
    entry = entries[0]

    # --- Title -----------------------------------------------------------
    title_el = entry.find("atom:title", _NS)
    video_title = (
        title_el.text.strip() if title_el is not None and title_el.text
        else "Untitled Video"
    )

    # --- Video URL (the watch link) --------------------------------------
    link_el = entry.find("atom:link[@rel='alternate']", _NS)
    video_url = link_el.get("href", "") if link_el is not None else ""

    # --- Description (media:group → media:description) -------------------
    media_group = entry.find("media:group", _NS)
    description = ""
    if media_group is not None:
        desc_el = media_group.find("media:description", _NS)
        if desc_el is not None and desc_el.text:
            description = desc_el.text

    # --- Extract job links (blacklist-filtered) --------------------------
    job_urls = _extract_job_urls(description)

    if job_urls:
        logger.info(
            "Video '%s' — %d job link(s) extracted.", video_title, len(job_urls),
        )
    else:
        logger.info(
            "Video '%s' — no job links found in description.", video_title,
        )

    # --- Build results ---------------------------------------------------
    results: list[dict] = []
    for url in job_urls:
        results.append(
            {
                "title": video_title,
                "url": url,
                "apply_url": url,       # key used by dedup.filter_new()
                "source": "youtube",
                "video_url": video_url,  # source video for email rendering
            }
        )

    logger.info("Stream C total: %d job link(s).", len(results))
    return results
