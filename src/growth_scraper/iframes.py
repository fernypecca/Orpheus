"""Extract iframes and capture their content (polite, robots-respecting).

Reports the page's iframes (`frames`) and fetches the text of each frame's
`src` (`frameTexts`). Fetching the content of an iframe requires crossing the
iframe's origin boundary, which is a *request to a third party* — so we do a
single polite GET (no script execution), honor robots.txt, and cap volume.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import config as cfgmod
from .utils import build_headers

_MAX_FRAMES = 5
_FETCH_TIMEOUT_S = 5.0
_CONCURRENCY = 3
_MAX_FRAME_TEXT = 2000
_MAX_FRAME_BYTES = 65_536

# Tracking/analytics/tag-manager hosts whose iframes carry no content value
# (e.g. GTM's noscript iframe appears on every page). Filtered out so they
# neither appear in `frames` nor get fetched.
_TRACKER_HOSTS = frozenset(
    host.lstrip(".")
    for host in (
        "googletagmanager.com",
        "google-analytics.com",
        "googlesyndication.com",
        "doubleclick.net",
        "googleadservices.com",
        "facebook.net",
        "facebook.com/tr",
        "hotjar.com",
        "crazyegg.com",
        "clarity.ms",
    )
)


def _is_tracker(src: str) -> bool:
    host = urlparse(src).netloc.lower().split(":")[0]
    for tracker in _TRACKER_HOSTS:
        if host == tracker or host.endswith("." + tracker):
            return True
    return False


def extract_iframes(url: str, html: str, max_frames: int = _MAX_FRAMES):
    """Return a list of {src, title, crossOrigin} for the page's iframes."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        frames = []
        for iframe in soup.find_all("iframe"):
            src = (iframe.get("src") or "").strip()
            if not src:
                continue
            src = urljoin(url, src)
            if urlparse(src).scheme not in ("http", "https"):
                continue
            if _is_tracker(src):
                continue
            frame_origin = urlparse(src).netloc
            page_origin = urlparse(url).netloc
            cross_origin = frame_origin != page_origin
            frames.append(
                {
                    "src": src,
                    "title": (iframe.get("title") or "").strip() or None,
                    "crossOrigin": cross_origin,
                }
            )
            if len(frames) >= max_frames:
                break
        return frames
    except Exception:
        return []


async def _polite_get_text(url: str, headers: dict) -> str:
    import httpx

    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= _MAX_FRAME_BYTES:
                    break
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(b"".join(chunks).decode("utf-8", errors="replace"), "html.parser")
    text = soup.get_text(" ", strip=True)
    if not text:
        text = _extract_title(soup, url)
    return text


_GENERIC_TITLES = frozenset(
    {"just a moment", "attention required", "verify you are human", "access denied"}
)


def _extract_title(soup, url: str) -> str:
    """Best title for a frame that renders no text (e.g. a video embed).

    Prioritizes og:title / meta name=title, then <title>. Skips generic shells
    ("just a moment", "YouTube") and the host's own brand, so we never return a
    misleading label for an empty shell.
    """
    candidates = []
    for attr in ("property", "name"):
        for val in ("og:title", "title", "twitter:title"):
            tag = soup.find("meta", attrs={attr: val})
            if tag and tag.get("content"):
                candidates.append(tag["content"].strip())
    if soup.title and soup.title.string:
        candidates.append(soup.title.string.strip())
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    brand = parts[-2] if len(parts) >= 2 else host
    for c in candidates:
        low = c.lower()
        if low and low != brand and low not in _GENERIC_TITLES:
            return c
    return ""


async def fetch_frame_texts(srcs, cfg, robots, limit: int = _MAX_FRAME_TEXT):
    """Fetch each frame src text with a polite GET. Fail-open per frame."""
    headers = build_headers(cfg)
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = []

    async def fetch(src):
        async with sem:
            if not await robots.is_allowed(src):
                results.append({"src": src, "text": "Skipped by robots"})
                return
            try:
                text = await asyncio.wait_for(
                    _polite_get_text(src, headers), timeout=_FETCH_TIMEOUT_S
                )
                results.append({"src": src, "text": text[:limit]})
            except asyncio.TimeoutError:
                results.append({"src": src, "text": "Error: timeout"})
            except Exception:
                results.append({"src": src, "text": "Error: fetch"})

    await asyncio.gather(*(fetch(s) for s in srcs))
    return results