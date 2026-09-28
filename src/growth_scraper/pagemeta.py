"""Cheap page inventory: links + image metadata (Context.dev images/links).

Pure bs4 over the raw HTML — no network, no browser. Fail-open: [] on any
error. Used for the `links` and `imagesInfo` record fields.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from .config import NAV_CONTAINER_SELECTORS

_WS = re.compile(r"\s+")

_MAX_LINKS = 200
_MAX_IMAGES = 20


def _clean(text: str, limit: int = 120) -> str:
    return _WS.sub(" ", text or "").strip()[:limit]


def _as_int(value) -> int | None:
    try:
        n = int(str(value).strip())
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _nav_elements(soup) -> set:
    """Elements forming site chrome (menus, header/footer). Anchors inside
    these are navigation, not content — skipped by extract_links."""
    found = set()
    try:
        for selector in NAV_CONTAINER_SELECTORS:
            try:
                for el in soup.select(selector):
                    found.add(el)
            except Exception:
                continue
    except Exception:
        pass
    return found


def _in_nav(a, nav_els: set) -> bool:
    p = a.parent
    while p is not None:
        if p in nav_els:
            return True
        p = p.parent
    return False


def extract_links(html: str, base_url: str, limit: int = _MAX_LINKS) -> list[dict]:
    """[{url, text}] for http(s) CONTENT anchors, deduped, in page order.

    Anchors inside navigation containers (menus, header/footer) are skipped:
    on SaaS pages they outnumber content links 10:1 and drown the inventory.
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        nav_els = _nav_elements(soup)
        out, seen = [], set()
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            if nav_els and _in_nav(a, nav_els):
                continue
            url = urljoin(base_url, href)
            if urlparse(url).scheme not in ("http", "https"):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "text": _clean(a.get_text(" ", strip=True))})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def extract_images_info(html: str, base_url: str, limit: int = _MAX_IMAGES) -> list[dict]:
    """[{url, alt, width, height}] — dimensions from HTML attrs when present."""
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        out, seen = [], set()
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if not src or src.startswith("data:"):
                srcset = img.get("srcset")
                if srcset:
                    cands = [c.strip().rsplit(" ", 1) for c in srcset.split(",") if c.strip()]
                    cands = [c[0] for c in cands if c and c[0]]
                    src = cands[-1] if cands else ""
                else:
                    continue
            if not src or src.startswith("data:"):
                continue
            url = urljoin(base_url, src)
            if url in seen:
                continue
            seen.add(url)
            out.append({
                "url": url,
                "alt": _clean(img.get("alt") or ""),
                "width": _as_int(img.get("width")),
                "height": _as_int(img.get("height")),
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
