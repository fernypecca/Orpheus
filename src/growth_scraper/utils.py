"""Utility functions shared across growth_scraper modules."""

from __future__ import annotations

from .config import ROBOTS_UA_TOKEN


def build_headers(cfg=None) -> dict:
    """Build polite request headers for extra fetches (frames/images).

    Honors cfg.extra_headers (opt-in overrides) and cfg.accept_language.
    """
    headers = {
        "User-Agent": ROBOTS_UA_TOKEN,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "X-Crawl4AI-Untouched": "1",
    }
    try:
        extra = getattr(cfg, "extra_headers", None) or {}
        for k, v in extra.items():
            if k and v is not None:
                headers[str(k)] = str(v)
        lang = getattr(cfg, "accept_language", None)
        if lang:
            headers["Accept-Language"] = str(lang)
    except Exception:
        pass
    return headers


def browser_headers(cfg=None) -> dict:
    """Headers for the Playwright browser (honest UA + opt-in extras)."""
    return build_headers(cfg)