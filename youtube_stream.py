"""
youtube_stream.py
=================
Stream C — YouTube RSS link extraction for the OnlineStudy4u channel.

Fetches the most recent video entries from the channel's public Atom feed,
extracts URLs embedded in video descriptions via regex, and returns them as
job-like dictionaries that plug straight into the dedup pipeline.

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
_MAX_VIDEOS: int = 3

# ---------------------------------------------------------------------------
# XML namespace map (YouTube Atom feeds use the media:* namespace)
# ---------------------------------------------------------------------------

_NS: dict[str, str] = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"(https?://[^\s]+)")


def _extract_urls(text: str) -> list[str]:
    """
    Pull **every** ``http(s)://…`` URL from *text*.

    No filtering or blacklisting is applied — the caller wants 100% of the
    links found in the video description.
    """
    if not text:
        return []
    return _URL_RE.findall(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrape_youtube_links() -> list[dict]:
    """
    Fetch the YouTube RSS feed for OnlineStudy4u and return job-like dicts.

    Each returned dict has the shape expected by ``dedup.filter_new``::

        {
            "title":     "[OnlineStudy4u] <video title>",
            "url":        <extracted link>,
            "apply_url":  <same link — used by dedup.filter_new>,
            "source":    "youtube",
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

    # Only process the N most recent videos
    entries = entries[:_MAX_VIDEOS]
    logger.info(
        "Processing %d most recent video(s) from feed.", len(entries)
    )

    results: list[dict] = []

    for entry in entries:
        # --- Title -----------------------------------------------------------
        title_el = entry.find("atom:title", _NS)
        video_title = (
            title_el.text.strip() if title_el is not None and title_el.text
            else "Untitled Video"
        )

        # --- Description (media:group → media:description) -------------------
        media_group = entry.find("media:group", _NS)
        description = ""
        if media_group is not None:
            desc_el = media_group.find("media:description", _NS)
            if desc_el is not None and desc_el.text:
                description = desc_el.text

        # --- Extract and filter URLs from description ------------------------
        urls = _extract_urls(description)
        if not urls:
            logger.debug(
                "No qualifying links found in video: %s", video_title
            )
            continue

        logger.info(
            "Video '%s' — %d link(s) extracted.", video_title, len(urls)
        )

        for url in urls:
            results.append(
                {
                    "title": f"[OnlineStudy4u] {video_title}",
                    "url": url,
                    "apply_url": url,   # key used by dedup.filter_new()
                    "source": "youtube",
                }
            )

    logger.info(
        "Stream C total: %d link(s) extracted from %d video(s).",
        len(results),
        len(entries),
    )
    return results
