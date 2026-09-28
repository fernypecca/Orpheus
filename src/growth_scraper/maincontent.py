"""Main-content extraction, readability-lite (Context.dev `mainContentOnly`).

No new dependencies: scores <article>/<main> and block containers by
text-density (visible text minus link text) after dropping boilerplate
(sidebars, comments, related, share boxes). Fail-open: None when nothing
convincing is found, and the caller keeps the full text.
"""

from __future__ import annotations

import re

_NOISE_SELECTORS = [
    ".sidebar", ".comments", ".comment", ".related", ".share", ".sharing",
    ".newsletter", ".subscribe", ".breadcrumb", ".pagination", ".tags",
    ".author-box", ".widget", "#sidebar", "#comments", "#related",
    "[role='complementary']", "[role='banner']", "[role='contentinfo']",
]

_MAIN_SELECTORS = ["main", "article", "[role='main']"]

_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def extract_main_text(html: str, max_chars: int = 0) -> str | None:
    """Best-effort main-column text, or None (caller keeps full text)."""
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        total = len(_clean(soup.get_text(" ", strip=True)))
        for sel in _NOISE_SELECTORS:
            try:
                for el in soup.select(sel):
                    el.decompose()
            except Exception:
                continue

        for sel in _MAIN_SELECTORS:
            try:
                el = soup.select_one(sel)
            except Exception:
                el = None
            if el:
                text = _clean(el.get_text(" ", strip=True))
                if len(text) >= 200 and _share(text, total):
                    return _cap(text, max_chars)

        best, best_score = None, 0
        for tag in ("article", "section", "div", "main"):
            for el in soup.find_all(tag):
                text = _clean(el.get_text(" ", strip=True))
                if len(text) < 200:
                    continue
                links = _clean(" ".join(
                    a.get_text(" ", strip=True) for a in el.find_all("a")
                ))
                score = len(text) - len(links)
                if score > best_score:
                    best, best_score = text, score
        if best and best_score >= 200 and _share(best, total):
            return _cap(best, max_chars)
        return None
    except Exception:
        return None


def _share(text: str, total: int) -> bool:
    """Guard: the winner must hold a fair share of the page.

    On listing pages (blog index, pricing grid) no single card IS the page —
    returning one card silently drops the rest (seen live on stripe.com/blog:
    61 words out of 1000+). Below 30%, keep the full text instead.
    """
    if total <= 0:
        return True
    return len(text) / total >= 0.30


def _cap(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        return cut.rsplit(" ", 1)[0] if " " in cut else cut
    return text
