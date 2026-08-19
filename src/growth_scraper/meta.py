"""Language detection + rich metadata extraction (pure HTML, no deps).

Fail-open by design: any error returns None / a dict with None values, never
raises. Consumed by the pipeline in run_one.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import LANG_STOPWORDS

_WORD_RE = re.compile(r"[a-zà-ü0-9']+")

META_KEYS = (
    "canonical", "ogTitle", "ogDescription", "ogImage",
    "twitterCard", "author", "publishedAt", "favicon",
)


def detect_language(html: str, text: str) -> str | None:
    """ISO 639-1 code, or None. Priority: <html lang> -> content-language
    meta -> stopword classification of `text` (needs enough signal)."""
    lang = _lang_from_attributes(html)
    if lang:
        return lang
    return _detect_by_content(text)


def _lang_from_attributes(html: str) -> str | None:
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        html_node = soup.find("html")
        if html_node and html_node.get("lang"):
            lang = str(html_node["lang"]).split("-")[0].strip().lower()
            if lang and len(lang) == 2:
                return lang
        for sel in ("meta[http-equiv='content-language']",
                    "meta[name='language']"):
            node = soup.select_one(sel)
            if node and node.get("content"):
                lang = str(node["content"]).split(",")[0].split("-")[0].strip().lower()
                if lang and len(lang) == 2:
                    return lang
    except Exception:
        pass
    return None


def _detect_by_content(text: str) -> str | None:
    tokens = _WORD_RE.findall(text.lower())
    if len(tokens) < 80:
        return None
    scores = {
        lang: sum(1 for t in tokens if t in sw)
        for lang, sw in LANG_STOPWORDS.items()
    }
    best_lang, best = max(scores.items(), key=lambda kv: kv[1])
    if best == 0:
        return None
    if best / len(tokens) < 0.05:
        return None
    rest = [v for l, v in scores.items() if l != best_lang]
    if rest and max(rest) and best / max(rest) < 1.5:
        return None
    return best_lang


def extract_meta(html: str, url: str) -> dict:
    """Stable-shape dict (all keys present, None when absent). Fail-open."""
    result = {k: None for k in META_KEYS}
    if not html:
        return result
    try:
        soup = BeautifulSoup(html, "html.parser")

        def value(sel: str) -> str | None:
            node = soup.select_one(sel)
            if node is None:
                return None
            raw = node.get("content") or node.get("href") or ""
            return " ".join(raw.strip().split()) or None

        result["canonical"] = _absolute(url, value("link[rel='canonical']"))
        result["ogTitle"] = value("meta[property='og:title']")
        result["ogDescription"] = value("meta[property='og:description']")
        result["ogImage"] = _absolute(url, value("meta[property='og:image']"))
        result["twitterCard"] = value("meta[name='twitter:card']")
        result["author"] = value("meta[name='author']") or value("meta[property='article:author']")
        result["publishedAt"] = value("meta[property='article:published_time']") or value("meta[name='date']")
        result["favicon"] = _absolute(url, value("link[rel~='icon']"))
    except Exception:
        pass
    return result


def _absolute(base: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urljoin(base, value)
    except Exception:
        return None