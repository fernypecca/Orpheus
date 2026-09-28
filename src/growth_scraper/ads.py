"""Ad-library card extraction (Google/Facebook/LinkedIn transparency).

`detect_library(url)` identifies the library from the URL alone (no fetch).
`extract_ads(html, url)` returns normalized cards:
  {library, advertiser, creative_url, image_url, image_width, image_height,
   format ("image" | "video" | None), position, total}

Status per library (sep-2026, verified live):
- google (adstransparency.google.com): FULL parser from rendered DOM —
  `creative-preview` cards with /creative/ URLs, tpc.googlesyndication.com
  images, `videocam` video marker, aria-label "Anuncio (N de M)".
- facebook (facebook.com/ads/library): robots.txt `Disallow: /` for * (Orpheus
  refuses by default) AND headless gets HTTP 403 even with override. No live
  DOM was obtainable, so card parsing is a documented stub returning [] —
  filling it needs a live-DOM pass, not guesses.
- linkedin (linkedin.com/ad-library*): login-walled in practice; same stub
  policy as Facebook.

Fail-open everywhere: unknown markup yields [] (never raises). No login, no
anti-bot bypass, ever.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def detect_library(url: str) -> str | None:
    """'google' | 'facebook' | 'linkedin' | None, from URL only."""
    try:
        host = (urlparse(url).hostname or "").lower()
        path = (urlparse(url).path or "").lower()
    except Exception:
        return None
    if "adstransparency.google.com" in host:
        return "google"
    if "facebook.com" in host and "/ads/library" in path:
        return "facebook"
    if "linkedin.com" in host and "ad-library" in path:
        return "linkedin"
    return None


def _google_ads(soup, base_url: str) -> list[dict]:
    out: list[dict] = []
    try:
        cards = soup.select("creative-preview")
    except Exception:
        return []
    for card in cards:
        try:
            link = card.select_one("a[href*='/creative/']")
            if not link:
                continue
            creative_url = urljoin(base_url, link.get("href", ""))
            label = link.get("aria-label") or ""
            m = re.search(r"\(\s*(\d+)\s+de\s+(\d+)\s*\)", label)
            position = int(m.group(1)) if m else None
            total = int(m.group(2)) if m else None
            img = card.select_one("img[src]")
            image_url = urljoin(base_url, img.get("src", "")) if img else None
            text = _clean(card.get_text(" ", strip=True))
            fmt = "video" if "videocam" in text else ("image" if image_url else None)
            name_el = card.select_one(".advertiser-name")
            advertiser = _clean(name_el.get_text(" ", strip=True)) if name_el else None
            out.append({
                "library": "google",
                "advertiser": advertiser,
                "creative_url": creative_url or None,
                "image_url": image_url,
                "image_width": _as_int(img.get("width")) if img else None,
                "image_height": _as_int(img.get("height")) if img else None,
                "format": fmt,
                "position": position,
                "total": total,
            })
        except Exception:
            continue
    return out


def _as_int(value) -> int | None:
    try:
        n = int(str(value).strip())
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def extract_ads(html: str, url: str) -> list[dict]:
    """Normalized ad cards for the page's library, or [] (fail-open)."""
    if not html or not detect_library(url):
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        library = detect_library(url)
        if library == "google":
            return _google_ads(soup, url)
        # facebook/linkedin: documented stubs (see module docstring).
        return []
    except Exception:
        return []
