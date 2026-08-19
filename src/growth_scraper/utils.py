"""Utility functions shared across growth_scraper modules."""

from __future__ import annotations

from .config import ROBOTS_UA_TOKEN


def build_headers(cfg) -> dict:
    """Build polite request headers for frame fetching."""
    return {
        "User-Agent": ROBOTS_UA_TOKEN,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "X-Crawl4AI-Untouched": "1",
    }


def custom_exception_handler(coro):
    """Wrap an async coroutine to capture exceptions as (result, status, error)."""

    async def wrapper():
        try:
            result = await coro
            return result, 200, None
        except Exception as exc:
            return None, 0, str(exc)

    return wrapper