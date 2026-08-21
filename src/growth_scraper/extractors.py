"""P1: page-type extractors — listing vs profile vs generic.

Instead of one flat text extractor, classify the page and extract structured
data where it makes sense:
- listing: repeated cards/items -> `items` with title/href/snippet
- profile: single entity -> title, description, dt/dd fields, contact info
- generic: plain text (crawl4ai's markdown is already used as `text`)
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import MAX_LISTING_ITEMS

_LISTING_SELECTORS = [
    ("article", "article"),
    ("li", "li"),
    ("card", "[class*='card'], [class*='item'], [class*='result'], [class*='product']"),
    ("row", "tr"),
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[ -]?)?(?:\(\d{2,4}\)|\d{2,4})[ -]?\d{3,4}[ -]?\d{3,4}")


def classify(soup: BeautifulSoup) -> str:
    # Profile signals first: a single-entity page (vendor profile, product
    # page) can still contain >=4 <li>/<article> from an unrelated nav,
    # header account menu, or photo carousel — those must not force a
    # "listing" classification over a real single-entity page. Verified live:
    # a real vendor profile (bodas.net) has a header login/register list +
    # a photo-carousel <li> set that alone clear the listing threshold.
    if soup.select_one("dt") or soup.select_one("[itemprop='name']") or soup.select_one("address"):
        return "profile"
    best_selector, best_count = None, 0
    for _name, selector in _LISTING_SELECTORS:
        count = len(soup.select(selector))
        if count > best_count:
            best_selector, best_count = selector, count
    if best_count >= 4:
        return "listing"
    return "generic"


def _pick_selector(soup: BeautifulSoup) -> str:
    best_selector, best_count = None, 0
    for _name, selector in _LISTING_SELECTORS:
        count = len(soup.select(selector))
        if count > best_count:
            best_selector, best_count = selector, count
    return best_selector or "article"


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_items(soup: BeautifulSoup, base_url: str) -> list[dict]:
    selector = _pick_selector(soup)
    items: list[dict] = []
    for block in soup.select(selector)[:MAX_LISTING_ITEMS]:
        if block.find("script") or block.find("style"):
            continue
        title_el = block.select_one("h2, h3, h4, a, [itemprop='name'], .title, .name")
        title = _clean(title_el.get_text(" ", strip=True)) if title_el else ""
        href = ""
        a = block.select_one("a[href]")
        if a:
            href = urljoin(base_url, a.get("href", ""))
        snippet = _clean(block.get_text(" ", strip=True))
        if not title:
            # fallback: use first non-empty text segment as pseudo-title
            title = snippet[:120]
        if not title and not href:
            continue
        items.append({"title": title, "href": href, "snippet": snippet})
    return items


def extract_profile(soup: BeautifulSoup, base_url: str) -> dict:
    h1 = soup.select_one("h1")
    title = _clean(h1.get_text(" ", strip=True)) if h1 else ""
    meta = soup.select_one("meta[name='description']")
    description = _clean(meta.get("content", "")) if meta else ""
    text = _clean(soup.get_text(" ", strip=True))

    fields = {}
    for dt, dd in zip(soup.select("dt"), soup.select("dt ~ dd")):
        k = _clean(dt.get_text(" ", strip=True))
        v = _clean(dd.get_text(" ", strip=True))
        if k and v:
            fields[k] = v

    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    phones = list(dict.fromkeys(_PHONE_RE.findall(text)))[:5]
    address = ""
    addr = soup.select_one("[itemprop='address'], address")
    if addr:
        address = _clean(addr.get_text(" ", strip=True))

    out = {"title": title, "description": description}
    if fields:
        out["fields"] = fields
    if emails:
        out["emails"] = emails
    if phones:
        out["phones"] = phones
    if address:
        out["address"] = address
    return out


def run_extraction(result) -> tuple[str, list[dict]]:
    """Returns (page_type, items_or_profile). Generic pages yield []."""
    html = result.cleaned_html or result.html or ""
    if not html:
        return "generic", []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return "generic", []
    base_url = getattr(result, "url", "") or ""
    ptype = classify(soup)
    if ptype == "listing":
        return ptype, extract_items(soup, base_url)
    if ptype == "profile":
        return ptype, [extract_profile(soup, base_url)]
    return ptype, []


# Sources structured.py trusts enough to override the DOM-heuristic classify()
# above. "meta"/"heuristic" are guesses too weak to arbitrate a disagreement.
_TRUSTED_STRUCTURED_SOURCES = ("jsonld", "microdata")


def reconcile_page_type(page_type: str, items: list[dict], structured: dict | None) -> tuple[str, list[dict]]:
    """classify() only sees DOM shape (li/article/card counts, dt/address tags),
    so a profile page with a busy header nav or photo carousel can still read
    as "listing" — verified live on bodas.net, whose vendor profiles have no
    <dt>/itemprop/<address> for classify() to catch, but do publish real
    LocalBusiness JSON-LD. When structured.py found that same page via a
    trusted source and disagrees, its answer wins: real schema.org markup
    beats a tag-counting guess. Stale "listing" items (nav links, photo
    captions) are dropped rather than shown alongside the corrected type.
    """
    if not structured or structured.get("source") not in _TRUSTED_STRUCTURED_SOURCES:
        return page_type, items
    structured_type = structured.get("entityType")
    if not structured_type or structured_type == page_type:
        return page_type, items
    if structured_type == "profile":
        return "profile", []
    if structured_type == "listing":
        return "listing", items
    return page_type, items