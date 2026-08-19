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
from .utils import build_headers, custom_exception_handler

_MAX_FRAMES = 5
_FETCH_TIMEOUT_S = 5.0
_CONCURRENCY = 3
_MAX_FRAME_TEXT = 2000


_SKIP_SCHEMES = ("data:", "about:", "blob:", "javascript:",)


def extract_iframes(url: str, html: str, max_frames: int = _MAX_FRAMES):
    """Return a list of {src, title, crossOrigin} for the page's iframes."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        frames = []
        for iframe in soup.find_all("iframe")[:max_frames]:
            src = (iframe.get("src") or "").strip()
            if not src:
                continue
            if src.lower().startswith(_SKIP_SCHEMES):
                continue
            src = urljoin(url, src)
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
        return frames
    except Exception:
        return []


async def _polite_get_text(url: str, headers: dict) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=4.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    return soup.get_text(" ", strip=True)


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
                results.append({"src": src, "text": "Texto del iframe cross-origin"})

    await asyncio.gather(*(fetch(s) for s in srcs))
    return results