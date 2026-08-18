# Server mode + Extracción estructurada — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a warm-browser HTTP server (`gscrape serve`) that storefront-analyzer / landing-qa-tool consume via a hybrid `orpheus.sh` (server first, spawn fallback), plus schema-aware structured extraction (`structured` field, summary triage, CSV).

**Architecture:** FastAPI + uvicorn owns the event loop (D7) and a single warm `AsyncWebCrawler`; each `POST /scrape` runs `Pipeline.run_one` with a per-run `ScrapeConfig` (D26/D18). Structured extraction is a pure function over the raw HTML with a JSON-LD → microdata → meta/OG → heuristic pipeline (D28, fail-open). The hybrid wrapper keeps the existing exit-0/stdout contract, so no TS wrapper changes.

**Tech Stack:** Python 3.12, Crawl4AI (Playwright), FastAPI, uvicorn, httpx, BeautifulSoup, pytest + pytest-asyncio, bash.

**Spec:** `docs/superpowers/specs/2026-08-18-server-mode-structured-design.md`

---

## Task 0: Baseline check

**Goal:** Confirm the 24 existing tests pass before any change.

**Verify:** `uv run pytest tests/ -q` → `24 passed`

**Steps:**

- [ ] **Step 1: Run the suite**

```bash
uv run pytest tests/ -q
```

Expected: `24 passed`. If not, stop and fix the baseline first.

---

## Task 1: `structured.py` extractor + heuristic selectors + unit tests

**Goal:** A pure, fail-open structured extractor over raw HTML, with tunable heuristic selectors in `config.py`.

**Files:**
- Create: `src/growth_scraper/structured.py`
- Modify: `src/growth_scraper/config.py` (selectors + cap constant)
- Test: `tests/test_structured.py` (unit portion)

**Acceptance Criteria:**
- [ ] `extract_structured(html)` returns a dict with keys `entityType`, `source`, `name`, `description`, `image`, `price`, `rating`, `reviews` (cap 3), `category`, `contact`, `itemCount`
- [ ] Source priority: JSON-LD > microdata > meta/OG > heuristic (first source with ≥1 field wins)
- [ ] Never raises: empty HTML, broken JSON, or no-signal pages return `None`
- [ ] Config exposes `STRUCTURED_*_SELECTORS` and `STRUCTURED_MAX_JSONLD_BYTES`

**Verify:** `uv run pytest tests/test_structured.py -q` → all pass

**Steps:**

- [ ] **Step 1: Add heuristic selectors + cap to `config.py`**

Insert this block in `config.py` right after the `PROTECTED_HTML_FRAGMENTS` list (line ~117), before the `@dataclass class ScrapeConfig`:

```python
# ---------------------------------------------------------------------------
# Structured extraction (P2): heuristic selectors, used only when a page has no
# schema/meta signals. Multi-language on purpose (same convention as the click
# guard blacklist).
# ---------------------------------------------------------------------------
STRUCTURED_MAX_JSONLD_BYTES = 1_000_000  # guard against huge @graph blobs
STRUCTURED_PRICE_SELECTORS = [
    "[itemprop='price']", "[data-price]", ".price", "[class*='price']",
    ".precio", "[data-precio]",
]
STRUCTURED_RATING_SELECTORS = [
    "[itemprop='ratingValue']", "[class*='rating']", "[class*='stars']",
    "[data-rating]", "[aria-label*='stars']", ".valoracion",
]
STRUCTURED_REVIEW_COUNT_SELECTORS = [
    "[itemprop='reviewCount']", "[class*='review-count']", "[class*='reviews']",
    "[data-review-count]", ".num-resenas",
]
STRUCTURED_CATEGORY_SELECTORS = [
    ".breadcrumb", "[class*='breadcrumb']", "[aria-label='breadcrumb']",
    "nav[aria-label*='breadcrumb']", "[itemprop='itemListElement']",
]
STRUCTURED_PHONE_SELECTORS = [
    "[itemprop='telephone']", "a[href^='tel:']",
]
STRUCTURED_EMAIL_SELECTORS = [
    "[itemprop='email']", "a[href^='mailto:']",
]
STRUCTURED_ADDRESS_SELECTORS = [
    "[itemprop='address']", "address",
]
```

- [ ] **Step 2: Write the failing unit tests** in `tests/test_structured.py`

```python
"""Unit tests for the structured extractor (pure HTML parsing, no browser)."""

from growth_scraper.structured import extract_structured

JSONLD_HTML = '''<!doctype html><html><head><title>Fotografía Luna</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "Fotografía Luna",
  "description": "Fotógrafo de bodas en Madrid con 12 años de experiencia.",
  "image": "https://cdn.example.com/luna.jpg",
  "url": "https://luna.example.com",
  "telephone": "+34 610 000 111",
  "email": "hola@luna.example.com",
  "priceRange": "€€",
  "address": {"@type": "PostalAddress", "streetAddress": "Calle Luna 7", "addressLocality": "Madrid", "postalCode": "28004", "addressCountry": "ES"},
  "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.9", "bestRating": "5", "reviewCount": "127"},
  "review": [
    {"@type": "Review", "author": {"@type": "Person", "name": "María"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Un equipo maravilloso."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Juan"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Fotos espectaculares."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Ana"}, "reviewRating": {"@type": "Rating", "ratingValue": "4"}, "reviewBody": "Muy profesionales."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Luis"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Repetiremos sin duda."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Sara"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Recomendadísimos."}
  ]
}
</script></head><body><h1>Fotografía Luna</h1></body></html>'''

MICRODATA_HTML = '''<div itemscope itemtype="https://schema.org/LocalBusiness">
  <meta itemprop="name" content="Floristería Primavera">
  <meta itemprop="description" content="Flores para bodas en Valencia.">
  <meta itemprop="priceRange" content="€€€">
  <div itemprop="aggregateRating" itemscope itemtype="https://schema.org/AggregateRating">
    <meta itemprop="ratingValue" content="4.7">
    <meta itemprop="bestRating" content="5">
    <meta itemprop="reviewCount" content="88">
  </div>
  <span itemprop="telephone">+34 963 000 222</span>
  <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
    <meta itemprop="streetAddress" content="Calle Flor 2">
    <meta itemprop="addressLocality" content="Valencia">
  </div>
</div>'''

META_HTML = '''<meta property="og:title" content="Banquete Azahar">
<meta property="og:description" content="Banquetes de boda en Sevilla.">
<meta property="og:image" content="https://cdn.example.com/azahar.jpg">
<meta name="description" content="Banquetes de boda en Sevilla.">
<meta name="price" content="€80–€120">
<meta name="currency" content="EUR">
<h1>Banquete Azahar</h1>'''

HEURISTIC_HTML = '''<h1>Música en Directo</h1>
<div class="price">€600–€900</div>
<div class="rating" data-rating="4.8">4.8</div>
<span class="review-count">34</span>
<nav class="breadcrumb"><a href="/">Inicio</a> <a href="/musica">Música</a> <a href="/dj">DJ</a></nav>
<p>Contacto: dj@ejemplo.com</p>'''


def test_jsonld_priority_and_fields():
    s = extract_structured(JSONLD_HTML)
    assert s["source"] == "jsonld"
    assert s["entityType"] == "profile"
    assert s["name"] == "Fotografía Luna"
    assert s["rating"] == {"value": 4.9, "best": 5.0, "count": 127}
    assert s["price"] == {"value": "€€", "currency": None, "isRange": True}
    assert len(s["reviews"]) == 3  # capped at 3
    assert s["reviews"][0]["author"] == "María"
    assert s["reviews"][0]["rating"] == 5.0
    assert s["contact"]["phone"] == "+34 610 000 111"
    assert s["contact"]["email"] == "hola@luna.example.com"
    assert s["contact"]["address"]["street"] == "Calle Luna 7"
    assert s["contact"]["website"] == "https://luna.example.com"


def test_microdata():
    s = extract_structured(MICRODATA_HTML)
    assert s["source"] == "microdata"
    assert s["entityType"] == "profile"
    assert s["name"] == "Floristería Primavera"
    assert s["price"] == {"value": "€€€", "currency": None, "isRange": True}
    assert s["rating"] == {"value": 4.7, "best": 5.0, "count": 88}
    assert s["contact"]["phone"] == "+34 963 000 222"
    assert s["contact"]["address"]["locality"] == "Valencia"


def test_meta():
    s = extract_structured(META_HTML)
    assert s["source"] == "meta"
    assert s["name"] == "Banquete Azahar"
    assert s["description"] == "Banquetes de boda en Sevilla."
    assert s["price"] == {"value": "€80–€120", "currency": "EUR", "isRange": True}


def test_heuristic_fallback():
    s = extract_structured(HEURISTIC_HTML)
    assert s["source"] == "heuristic"
    assert s["price"] == {"value": "€600–€900", "currency": None, "isRange": True}
    assert s["rating"]["value"] == 4.8
    assert s["rating"]["count"] == 34
    assert s["contact"]["email"] == "dj@ejemplo.com"
    assert s["category"]


def test_empty_html_none():
    assert extract_structured("") is None


def test_no_signals_none():
    assert extract_structured("<html><body><p>hola</p></body></html>") is None


def test_broken_json_no_raise():
    assert extract_structured("<script type='application/ld+json'>{not json}</script>") is None


def test_listing_entity_itemcount():
    html = ('<script type="application/ld+json">'
            '{"@type":"ItemList","name":"Proveedores",'
            '"itemListElement":[{"@type":"ListItem","position":1},{"@type":"ListItem","position":2}]}'
            '</script>')
    s = extract_structured(html)
    assert s["source"] == "jsonld"
    assert s["entityType"] == "listing"
    assert s["itemCount"] == 2
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_structured.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'growth_scraper.structured'`.

- [ ] **Step 4: Create `src/growth_scraper/structured.py`**

```python
"""P2: structured entity extraction from raw HTML.

Pipeline: JSON-LD -> microdata -> meta/OG -> heuristic selectors. Fails open:
no signals (or any error) yields None, never raising.

Output (record.structured):
{
  "entityType": "profile" | "product" | "listing" | null,
  "source": "jsonld" | "microdata" | "meta" | "heuristic" | null,
  "name", "description", "image": str | null,
  "price": {"value": str | null, "currency": str | null, "isRange": bool} | null,
  "rating": {"value": float | null, "best": float | null, "count": int | null} | null,
  "reviews": [{"author": str, "rating": float | null, "text": str}],
  "category": str | null,
  "contact": {"phone": str | null, "email": str | null, "website": str | null,
              "address": {"street", "locality", "region", "postalCode", "country"} | null} | null,
  "itemCount": int | null
}
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from .config import (
    STRUCTURED_ADDRESS_SELECTORS,
    STRUCTURED_CATEGORY_SELECTORS,
    STRUCTURED_EMAIL_SELECTORS,
    STRUCTURED_MAX_JSONLD_BYTES,
    STRUCTURED_PHONE_SELECTORS,
    STRUCTURED_PRICE_SELECTORS,
    STRUCTURED_RATING_SELECTORS,
    STRUCTURED_REVIEW_COUNT_SELECTORS,
)

_ENTITY_TYPES = ("Product", "LocalBusiness", "ProfessionalService", "Service", "Organization", "Place")
_LISTING_TYPES = ("ItemList", "CollectionPage", "OfferCatalog")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[ -]?)?(?:\(\d{2,4}\)|\d{2,4})[ -]?\d{3,4}[ -]?\d{3,4}")

_ADDR_MAP = {
    "streetAddress": "street",
    "addressLocality": "locality",
    "addressRegion": "region",
    "postalCode": "postalCode",
    "addressCountry": "country",
}

_EMPTY = {
    "entityType": None, "source": None, "name": None, "description": None,
    "image": None, "price": None, "rating": None, "reviews": [],
    "category": None, "contact": None, "itemCount": None,
}


def _as_float(v) -> float | None:
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _as_int(v) -> int | None:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _clean(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


# -- JSON-LD ----------------------------------------------------------------

def _jsonld_entities(soup: BeautifulSoup) -> list[dict]:
    entities: list[dict] = []
    total = 0
    for script in soup.find_all("script"):
        stype = (script.get("type") or "").strip().lower()
        if "ld+json" not in stype:
            continue
        raw = script.string or script.get_text()
        total += len(raw)
        if total > STRUCTURED_MAX_JSONLD_BYTES:
            break
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("@graph"), list):
                entities.extend(g for g in item["@graph"] if isinstance(g, dict))
            else:
                entities.append(item)
    return entities


def _entity_kind(entity: dict) -> str | None:
    types = entity.get("@type")
    if not types:
        return None
    names = [types] if isinstance(types, str) else [t for t in types if isinstance(t, str)]
    for t in names:
        if t in _ENTITY_TYPES:
            return "profile"
        if t in _LISTING_TYPES:
            return "listing"
    return None


def _pick_entity(entities: list[dict]) -> dict | None:
    profile = next((e for e in entities if _entity_kind(e) == "profile"), None)
    if profile is not None:
        return profile
    return next((e for e in entities if _entity_kind(e) == "listing"), None)


def _ld_image(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("url") or v.get("@id") or ""
    if isinstance(v, list) and v:
        return _ld_image(v[0])
    return ""


def _ld_price(offers) -> dict | None:
    if not isinstance(offers, dict):
        return None
    value = offers.get("price") or offers.get("lowPrice")
    high = offers.get("highPrice")
    currency = offers.get("priceCurrency")
    if isinstance(value, list):
        value = value[0] if value else None
    is_range = bool(high) or (isinstance(value, str) and "-" in value)
    if high:
        value = f"{value}–{high}"
    if value is None:
        price_range = offers.get("priceRange") or ""
        if price_range:
            return {"value": _clean(price_range), "currency": _clean(currency), "isRange": True}
        return None
    return {"value": _clean(value), "currency": _clean(currency), "isRange": is_range}


def _ld_rating(ar) -> dict | None:
    if not isinstance(ar, dict):
        return None
    value = _as_float(ar.get("ratingValue"))
    best = _as_float(ar.get("bestRating"))
    count = _as_int(ar.get("reviewCount"))
    if value is None and count is None:
        return None
    return {"value": value, "best": best, "count": count}


def _ld_reviews(review) -> list[dict]:
    items = review if isinstance(review, list) else [review]
    out: list[dict] = []
    for r in items:
        if not isinstance(r, dict):
            continue
        author = r.get("author")
        author_name = author if isinstance(author, str) else (author.get("name", "") if isinstance(author, dict) else "")
        rating_el = r.get("reviewRating") or {}
        rating = _as_float(rating_el.get("ratingValue")) if isinstance(rating_el, dict) else None
        out.append({"author": _clean(author_name), "rating": rating, "text": _clean(r.get("reviewBody"))})
        if len(out) >= 3:
            break
    return out


def _ld_contact(entity: dict) -> dict | None:
    contact = None
    cp = entity.get("contactPoint")
    if isinstance(cp, dict):
        contact = cp
    elif isinstance(cp, list) and cp:
        contact = next((c for c in cp if isinstance(c, dict)), None)
    addr = entity.get("address")
    out: dict = {}
    if isinstance(contact, dict):
        if contact.get("telephone"):
            out["phone"] = _clean(contact["telephone"])
        if contact.get("email"):
            out["email"] = _clean(contact["email"])
    if entity.get("telephone") and not out.get("phone"):
        out["phone"] = _clean(entity["telephone"])
    if entity.get("email") and not out.get("email"):
        out["email"] = _clean(entity["email"])
    if isinstance(addr, dict):
        out["address"] = {
            "street": _clean(addr.get("streetAddress")),
            "locality": _clean(addr.get("addressLocality")),
            "region": _clean(addr.get("addressRegion")),
            "postalCode": _clean(addr.get("postalCode")),
            "country": _clean(addr.get("addressCountry")),
        }
    if entity.get("url"):
        out["website"] = _clean(entity["url"])
    return out or None


def _ld_category(entities: list[dict], entity: dict) -> str:
    cat = entity.get("category")
    if cat:
        return _clean(cat)
    for e in entities:
        if not isinstance(e.get("@type"), str) or e["@type"] != "BreadcrumbList":
            continue
        parts = []
        for it in e.get("itemListElement") or []:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or (it.get("item", {}) or {}).get("name")
            if name:
                parts.append(_clean(name))
        if parts:
            return " > ".join(parts)
    return ""


def _from_jsonld(soup: BeautifulSoup) -> dict | None:
    entities = _jsonld_entities(soup)
    entity = _pick_entity(entities)
    if entity is None:
        return None
    kind = _entity_kind(entity)
    out = dict(_EMPTY)
    out["entityType"] = "listing" if kind == "listing" else "profile"
    out["name"] = _clean(entity.get("name")) or None
    out["description"] = _clean(entity.get("description")) or None
    out["image"] = _ld_image(entity.get("image")) or None
    price = _ld_price(entity.get("offers"))
    if price is None and entity.get("priceRange"):
        price = {"value": _clean(entity["priceRange"]), "currency": None, "isRange": True}
    out["price"] = price
    out["rating"] = _ld_rating(entity.get("aggregateRating"))
    out["reviews"] = _ld_reviews(entity.get("review"))
    out["category"] = _ld_category(entities, entity) or None
    out["contact"] = _ld_contact(entity)
    if kind == "listing":
        items = entity.get("itemListElement")
        if isinstance(items, list):
            out["itemCount"] = len(items)
        else:
            out["itemCount"] = _as_int(entity.get("numberOfItems"))
    return out


# -- Microdata --------------------------------------------------------------

def _from_microdata(soup: BeautifulSoup) -> dict | None:
    scope = soup.select_one("[itemscope]")
    if scope is None:
        return None
    itemtype = " ".join(scope.get("itemtype", "")).lower()
    is_listing = any(t in itemtype for t in ("itemlist", "collectionpage", "offercatalog"))
    is_entity = is_listing or any(
        t in itemtype for t in ("product", "localbusiness", "professionalservice", "service", "organization", "place")
    )
    if not is_entity:
        return None
    props: dict[str, str] = {}
    for el in scope.select("[itemprop]"):
        if el is scope:
            continue
        names = el.get("itemprop", "").split()
        val = el.get("content")
        if val is None and el.get("datetime"):
            val = el.get("datetime")
        if val is None:
            val = el.get_text(" ", strip=True)
        for n in names:
            if n and n not in props:
                props[n] = _clean(val)
    if not props:
        return None
    out = dict(_EMPTY)
    out["entityType"] = "listing" if is_listing else "profile"
    out["source"] = "microdata"
    out["name"] = props.get("name") or None
    out["description"] = props.get("description") or None
    price = props.get("price") or props.get("priceRange")
    if price:
        out["price"] = {"value": price, "currency": props.get("priceCurrency") or None, "isRange": "-" in price}
    if props.get("ratingValue") or props.get("reviewCount"):
        out["rating"] = {
            "value": _as_float(props.get("ratingValue")),
            "best": _as_float(props.get("bestRating")),
            "count": _as_int(props.get("reviewCount")),
        }
    addr_keys = ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
    has_addr = any(props.get(k) for k in addr_keys)
    if has_addr or props.get("telephone") or props.get("email") or props.get("url"):
        out["contact"] = {
            "phone": props.get("telephone") or None,
            "email": props.get("email") or None,
            "website": props.get("url") or None,
            "address": {_ADDR_MAP[k]: props.get(k) or None for k in addr_keys} if has_addr else None,
        }
    out["category"] = props.get("category") or None
    if props.get("numberOfItems"):
        out["itemCount"] = _as_int(props.get("numberOfItems"))
    return out


# -- Meta / OG --------------------------------------------------------------

def _from_meta(soup: BeautifulSoup) -> dict | None:
    def meta(selector: str) -> str:
        el = soup.select_one(selector)
        return _clean(el.get("content")) if el else ""

    name = meta("meta[property='og:title']") or meta("meta[name='title']")
    desc = meta("meta[name='description']") or meta("meta[property='og:description']")
    image = meta("meta[property='og:image']")
    price = meta("meta[name='price']")
    currency = meta("meta[name='currency']")
    if not (name or desc or image or price):
        return None
    out = dict(_EMPTY)
    out["source"] = "meta"
    out["name"] = name or None
    out["description"] = desc or None
    out["image"] = image or None
    if price:
        out["price"] = {"value": price, "currency": currency or None, "isRange": "-" in price}
    return out


# -- Heuristic fallback -----------------------------------------------------

def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            t = _clean(el.get_text(" ", strip=True))
            if t:
                return t
    return ""


def _from_heuristic(soup: BeautifulSoup) -> dict | None:
    out = dict(_EMPTY)
    out["source"] = "heuristic"
    found = False

    price = _first_text(soup, STRUCTURED_PRICE_SELECTORS)
    if price:
        found = True
        out["price"] = {"value": price, "currency": None, "isRange": "-" in price}
    rv = _as_float(_first_text(soup, STRUCTURED_RATING_SELECTORS))
    rc = _as_int(_first_text(soup, STRUCTURED_REVIEW_COUNT_SELECTORS))
    if rv is not None or rc is not None:
        found = True
        out["rating"] = {"value": rv, "best": 5.0 if rv else None, "count": rc}
    category = _first_text(soup, STRUCTURED_CATEGORY_SELECTORS)
    if category:
        found = True
        out["category"] = category

    page_text = soup.get_text(" ", strip=True)
    email = _first_text(soup, STRUCTURED_EMAIL_SELECTORS)
    if not email:
        emails = _EMAIL_RE.findall(page_text)
        email = emails[0] if emails else ""
    phone = _first_text(soup, STRUCTURED_PHONE_SELECTORS)
    if not phone:
        phones = _PHONE_RE.findall(page_text)
        phone = phones[0] if phones else ""
    address = _first_text(soup, STRUCTURED_ADDRESS_SELECTORS)
    if phone or email or address:
        found = True
        addr = {"street": address, "locality": None, "region": None,
                "postalCode": None, "country": None} if address else None
        out["contact"] = {"phone": phone or None, "email": email or None,
                          "website": None, "address": addr}
    return out if found else None


# -- Entry point ------------------------------------------------------------

def extract_structured(html: str, summary: dict | None = None) -> dict | None:
    """Best-effort structured entity extraction. Never raises; None on no signals."""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        result = _from_jsonld(soup)
        if result is not None:
            result["source"] = "jsonld"
            return result
        result = _from_microdata(soup)
        if result is not None:
            return result
        result = _from_meta(soup)
        if result is not None:
            return result
        result = _from_heuristic(soup)
        if result is not None:
            return result
        return None
    except Exception:
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_structured.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/growth_scraper/structured.py src/growth_scraper/config.py tests/test_structured.py
git commit -m "feat: structured extractor (JSON-LD/microdata/meta/heuristic) + selectores"
```

---

## Task 2: Wire `structured` into the record, summary triage + CSV, fixture routes + e2e tests

**Goal:** Every record exposes `structured`; `summary` gets cheap triage fields; the CSV mirrors them; fixture pages + end-to-end tests prove it through the browser.

**Files:**
- Modify: `src/growth_scraper/pipeline.py:15` (import), `src/growth_scraper/pipeline.py:140-165` (`_build_summary`), `src/growth_scraper/pipeline.py:353` (hook)
- Modify: `src/growth_scraper/config.py:159-177` (`Record` dataclass), `src/growth_scraper/config.py:178-198` (`to_dict`)
- Modify: `src/growth_scraper/records.py:44-47` (HEADERS), `src/growth_scraper/records.py:55-70` (`write`)
- Modify: `tests/fixtureserver.py:39` (`build_pages`)
- Test: `tests/test_structured.py` (append e2e + CSV tests)

**Acceptance Criteria:**
- [ ] `record.structured` populated from JSON-LD/microdata/meta/heuristic for the fixture pages
- [ ] `summary` contains `structuredSource`, `structuredPrice`, `structuredRatingValue`, `structuredReviewCount`, `structuredCategory` when `structured` exists, and none of them when it doesn't
- [ ] CSV header + rows include the 5 `structured*` columns
- [ ] Existing 24 tests still green

**Verify:** `uv run pytest tests/ -q` → 24 old + new tests pass

**Steps:**

- [ ] **Step 1: Add `structured` + `fromCache` fields to `Record` and `to_dict`**

In `config.py`, in the `Record` dataclass after `rawHtml: Optional[str] = None` (line 176):

```python
    structured: Optional[dict] = None
    fromCache: bool = False
```

In `Record.to_dict()`, after the `"protectionBlocked": self.protectionBlocked,` line (line 192), add:

```python
            "structured": self.structured,
```

And after the `rawHtml` opt-in block at the end of `to_dict` (line 197-198), add:

```python
        if self.fromCache:
            d["fromCache"] = True
```

- [ ] **Step 2: Import `extract_structured` in pipeline.py**

At `pipeline.py:15`, change:

```python
from . import apipage, extractors, protection
```

to:

```python
from . import apipage, extractors, protection
from .structured import extract_structured
```

- [ ] **Step 3: Extend `_build_summary` to accept + surface structured triage**

Replace the whole `_build_summary` function (`pipeline.py:140-165`) with:

```python
def _build_summary(url: str, title: str, page_type: str, items: list, text: str, html: str,
                   structured: dict | None = None) -> dict:
    """Cheap structured triage fields so growth marketers can filter before the LLM."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    meta, h1 = "", ""
    if html:
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, "html.parser")
            m = soup.select_one("meta[name='description']")
            if m:
                meta = " ".join((m.get("content") or "").split())
            h = soup.select_one("h1")
            if h:
                h1 = " ".join(h.get_text(" ", strip=True).split())
        except Exception:
            pass
    result = {
        "domain": host,
        "title": title,
        "metaDescription": meta,
        "h1": h1,
        "wordCount": len(text.split()),
        "itemCount": len(items),
        "pageType": page_type,
    }
    if structured:
        price = structured.get("price") or {}
        rating = structured.get("rating") or {}
        result["structuredSource"] = structured.get("source")
        result["structuredPrice"] = price.get("value")
        result["structuredRatingValue"] = rating.get("value")
        result["structuredReviewCount"] = rating.get("count")
        result["structuredCategory"] = structured.get("category")
    return result
```

- [ ] **Step 4: Call `extract_structured` in `run_one` and pass it to the summary**

Replace the block at `pipeline.py:353`:

```python
        record.summary = _build_summary(url, record.title, record.pageType, record.items, record.text, raw_html)
```

with:

```python
        record.structured = extract_structured(raw_html)
        record.summary = _build_summary(url, record.title, record.pageType, record.items, record.text, raw_html, record.structured)
```

- [ ] **Step 5: Add CSV columns in records.py**

Replace `CsvWriter.HEADERS` (`records.py:44-47`) with:

```python
    HEADERS = [
        "url", "title", "pageType", "domain", "metaDescription", "h1",
        "wordCount", "itemCount", "statusCode", "error", "text_preview",
        "structuredSource", "structuredPrice", "structuredRatingValue",
        "structuredReviewCount", "structuredCategory",
    ]
```

Replace the `self._writer.writerow([...])` list in `CsvWriter.write` (`records.py:58-70`) with:

```python
        self._writer.writerow([
            record.url,
            record.title,
            record.pageType,
            s.get("domain", ""),
            s.get("metaDescription", ""),
            s.get("h1", ""),
            s.get("wordCount", 0),
            s.get("itemCount", 0),
            record.statusCode or "",
            record.error or "",
            preview,
            s.get("structuredSource", ""),
            s.get("structuredPrice", ""),
            s.get("structuredRatingValue", ""),
            s.get("structuredReviewCount", ""),
            s.get("structuredCategory", ""),
        ])
```

- [ ] **Step 6: Add fixture routes**

In `tests/fixtureserver.py`, inside `build_pages()`, right before the `"/withimg"` entry (line 155), add these routes:

```python
        "/structured-jsonld": page(
            "Fotografía Luna",
            """
            <h1>Fotografía Luna</h1>
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "LocalBusiness",
              "name": "Fotografía Luna",
              "description": "Fotógrafo de bodas en Madrid con 12 años de experiencia.",
              "priceRange": "€€",
              "telephone": "+34 610 000 111",
              "email": "hola@luna.example.com",
              "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.9", "bestRating": "5", "reviewCount": "127"},
              "review": [
                {"@type": "Review", "author": {"@type": "Person", "name": "María"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Un equipo maravilloso."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Juan"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Fotos espectaculares."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Ana"}, "reviewRating": {"@type": "Rating", "ratingValue": "4"}, "reviewBody": "Muy profesionales."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Luis"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Repetiremos sin duda."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Sara"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Recomendadísimos."}
              ]
            }
            </script>
            <p>Contenido visible de la página.</p>
            """,
        ),
        "/structured-microdata": page(
            "Floristería Primavera",
            """
            <h1>Floristería Primavera</h1>
            <div itemscope itemtype="https://schema.org/LocalBusiness">
              <meta itemprop="name" content="Floristería Primavera">
              <meta itemprop="description" content="Flores para bodas en Valencia.">
              <meta itemprop="priceRange" content="€€€">
              <div itemprop="aggregateRating" itemscope itemtype="https://schema.org/AggregateRating">
                <meta itemprop="ratingValue" content="4.7">
                <meta itemprop="bestRating" content="5">
                <meta itemprop="reviewCount" content="88">
              </div>
              <span itemprop="telephone">+34 963 000 222</span>
              <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
                <meta itemprop="streetAddress" content="Calle Flor 2">
                <meta itemprop="addressLocality" content="Valencia">
              </div>
            </div>
            """,
        ),
        "/structured-meta": page(
            "Banquete Azahar",
            """
            <meta property="og:title" content="Banquete Azahar">
            <meta property="og:description" content="Banquetes de boda en Sevilla.">
            <meta property="og:image" content="https://cdn.example.com/azahar.jpg">
            <meta name="description" content="Banquetes de boda en Sevilla.">
            <meta name="price" content="€80–€120">
            <meta name="currency" content="EUR">
            <h1>Banquete Azahar</h1>
            """,
        ),
        "/structured-heuristic": page(
            "Música en Directo",
            """
            <h1>Música en Directo</h1>
            <div class="price">€600–€900</div>
            <div class="rating" data-rating="4.8">4.8</div>
            <span class="review-count">34</span>
            <nav class="breadcrumb"><a href="/">Inicio</a> <a href="/musica">Música</a> <a href="/dj">DJ</a></nav>
            <p>Contacto: dj@ejemplo.com</p>
            """,
        ),
        "/structured-none": page("Página plana", "<h1>Página plana</h1><p>Sin datos estructurados.</p>"),
```

- [ ] **Step 7: Append e2e + CSV tests to `tests/test_structured.py`**

```python
from conftest import base_cfg, run, scrape_url


def test_e2e_jsonld_through_browser(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-jsonld")))
    s = rec.structured
    assert s["source"] == "jsonld"
    assert s["entityType"] == "profile"
    assert s["name"] == "Fotografía Luna"
    assert s["rating"]["count"] == 127
    assert len(s["reviews"]) == 3
    assert rec.summary["structuredRatingValue"] == 4.9
    assert rec.summary["structuredReviewCount"] == 127
    assert rec.summary["structuredSource"] == "jsonld"


def test_e2e_microdata_through_browser(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-microdata")))
    s = rec.structured
    assert s["source"] == "microdata"
    assert s["price"]["value"] == "€€€"
    assert s["contact"]["address"]["locality"] == "Valencia"


def test_e2e_heuristic_through_browser(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-heuristic")))
    s = rec.structured
    assert s["source"] == "heuristic"
    assert s["price"]["value"] == "€600–€900"
    assert rec.summary["structuredPrice"] == "€600–€900"


def test_e2e_no_signals_structured_none(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-none")))
    assert rec.structured is None
    assert "structuredSource" not in rec.summary


def test_csv_structured_columns(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    code = main([fs.url("/structured-jsonld"), "-o", str(out), "--csv",
                 "--delay", "0", "--jitter", "0"])
    assert code == 0
    csv_path = tmp_path / "cli.csv"
    header = csv_path.read_text().splitlines()[0]
    assert "structuredSource" in header
    assert "structuredRatingValue" in header
    row = csv_path.read_text().splitlines()[1]
    assert "Fotografía Luna" in row
    assert "jsonld" in row
```

- [ ] **Step 8: Run the full suite**

```bash
uv run pytest tests/ -q
```

Expected: 24 old tests + 13 new unit tests + 5 e2e tests all pass.

- [ ] **Step 9: Commit**

```bash
git add src/growth_scraper/pipeline.py src/growth_scraper/config.py src/growth_scraper/records.py tests/fixtureserver.py tests/test_structured.py
git commit -m "feat: structured en record + summary triage + CSV, fixtures y tests e2e"
```

---

## Task 3: Per-run `ScrapeConfig` in `Pipeline.run_one` (concurrency-safe)

**Goal:** `run_one` accepts an optional per-run `ScrapeConfig` so the server can serve concurrent requests with different options without mutating shared state.

**Files:**
- Modify: `src/growth_scraper/pipeline.py:28-38` (`Session`), `src/growth_scraper/pipeline.py:172` (`self.session` fallback), `src/growth_scraper/pipeline.py:213-242` (hooks), `src/growth_scraper/pipeline.py:263-375` (`run_one`)
- Test: existing `tests/` (regression)

**Acceptance Criteria:**
- [ ] `run_one(url, crawled_from=None, cfg=None)` uses the passed `cfg` (falls back to `self.cfg`) for every decision (robots, cache, retries, text cap, apis, images)
- [ ] Hooks (`after_goto`, `before_retrieve_html`) read `handle_consent`/`expand`/`capture_apis` from the **session's** cfg, not `self.cfg`
- [ ] Cached records come back with `fromCache = True`
- [ ] Existing 24 tests still green; cache test still proves no reprocess

**Verify:** `uv run pytest tests/ -q` → all green

**Steps:**

- [ ] **Step 1: Add `cfg` to `Session`**

In `Session.__init__` (`pipeline.py:34-38`), after `self.replayed: list[dict] = []`, add:

```python
        self.cfg: "ScrapeConfig | None" = None
```

- [ ] **Step 2: Default the fallback session's cfg**

In `Pipeline.__init__` (`pipeline.py:172`), change:

```python
        self.session = Session()  # fallback when a hook gets no session_id
```

to:

```python
        self.session = Session()  # fallback when a hook gets no session_id
        self.session.cfg = self.cfg
```

- [ ] **Step 3: Make the hooks use the session's cfg**

Replace `_hook_after_goto` (`pipeline.py:213-225`) with:

```python
    async def _hook_after_goto(self, page, context, url, config=None, **kwargs):
        session = self._session_for(config)
        cfg = session.cfg or self.cfg
        session.page = page
        try:
            if cfg.handle_consent:
                status = await handle_consent(page)
                emit_progress(cfg.verbose, f"consent on {url}: {status}")
            if cfg.expand:
                summary = await expand_and_scroll(page, cfg, session.netrec)
                emit_progress(cfg.verbose, f"probe on {url}: {summary}")
        except Exception as exc:
            emit_progress(cfg.verbose, f"after_goto probe failed for {url}: {exc}")
        return page
```

Replace `_hook_before_retrieve_html` (`pipeline.py:227-242`) with:

```python
    async def _hook_before_retrieve_html(self, page, context, config=None, **kwargs):
        # P1: the page is alive here and has already fired its XHRs; replay any
        # paginated internal APIs so we capture more than the default load.
        session = self._session_for(config)
        cfg = session.cfg or self.cfg
        if cfg.capture_apis and cfg.expand and session.page_url:
            try:
                session.netrec.deactivate()  # replay fetches must not be re-captured
                captured = session.netrec.snapshot()
                session.replayed = await apipage.replay_pagination(
                    page, session.page_url, captured
                )
                if session.replayed:
                    emit_progress(cfg.verbose, f"pagination replay: +{len(session.replayed)} responses")
            except Exception as exc:
                emit_progress(cfg.verbose, f"pagination replay failed: {exc}")
        return page
```

- [ ] **Step 4: Rewrite `run_one` to use the per-run `cfg`**

Replace the entire `run_one` method (`pipeline.py:263-375`) with:

```python
    async def run_one(self, url: str, crawled_from: str | None = None,
                      cfg: ScrapeConfig | None = None) -> Record:
        cfg = cfg or self.cfg
        record = Record(url=url, crawledFrom=crawled_from)

        # robots.txt (default: respected)
        if not cfg.ignore_robots:
            allowed = await self.robots.is_allowed(url)
            if not allowed:
                record.error = "ROBOTS_BLOCKED: disallowed by robots.txt"
                emit_progress(cfg.verbose, f"robots.txt blocks {url}")
                return record

        # local record cache
        if cfg.cache_dir and not crawled_from:
            cached = self._cache_read(url)
            if cached is not None:
                cached.crawledFrom = crawled_from
                cached.fromCache = True
                emit_progress(cfg.verbose, f"cache hit: {url}")
                return cached

        emit_progress(cfg.verbose, f"crawling {url}")

        # One browser context per run so concurrent crawls don't share state.
        attempts = 1 + cfg.max_retries
        result = None
        last_exc: Exception | None = None
        used_session: Session | None = None
        for attempt in range(attempts):
            sid = uuid.uuid4().hex
            session = Session()
            session.page_url = url
            session.cfg = cfg
            session.netrec.reset(url)  # starts the recorder active for this page
            self._sessions[sid] = session
            run_cfg = copy.copy(self.run_cfg)
            run_cfg.session_id = sid
            try:
                result = await self.crawler.arun(url, config=run_cfg)
                if result is None:
                    raise RuntimeError("crawl4ai returned no result")
                status = getattr(result, "status_code", None)
                if status is not None and status >= 500 and attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: status {status} (attempt {attempt + 1}/{attempts})")
                    result = None
                    continue
                used_session = session
                break
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: {exc}")
                    await asyncio.sleep(cfg.retry_backoff * (2 ** attempt))
                    continue
                break
            finally:
                self._sessions.pop(sid, None)
                try:
                    await self.crawler.crawler_strategy.kill_session(sid)
                except Exception:
                    pass

        if result is None:
            record.error = f"CRAWL_ERROR: {last_exc or 'no result after retries'}"
            return record

        record.statusCode = getattr(result, "status_code", None)
        if record.statusCode is not None and record.statusCode >= 500:
            record.error = f"HTTP_ERROR: {record.statusCode} after {attempts} attempt(s)"
            emit_progress(cfg.verbose, record.error)
            return record

        self._last_result = result
        raw_html = getattr(result, "html", "") or ""
        setattr(record, "_raw_html", raw_html)
        if cfg.raw_html:
            record.rawHtml = raw_html

        # fail-closed anti-bot detection
        reason = protection.detect(result)
        if reason:
            record.error = reason
            record.protectionBlocked = True
            emit_progress(cfg.verbose, reason)
            return record

        record.title = _title_from_result(result)
        record.text = _text_from_result(result, fit_text=cfg.fit_text, max_chars=cfg.max_text_chars)

        # P1: page-type extractors
        if result.cleaned_html or result.html:
            record.pageType, record.items = extractors.run_extraction(result)

        # P2: structured entities (JSON-LD -> microdata -> meta -> heuristic)
        record.structured = extract_structured(raw_html)
        record.summary = _build_summary(url, record.title, record.pageType, record.items, record.text, raw_html, record.structured)

        # P0/P1: API responses + pagination replay (deduped by URL)
        if cfg.capture_apis and used_session is not None:
            seen_urls: set[str] = set()
            api_responses: list[dict] = []
            for entry in used_session.netrec.snapshot() + used_session.replayed:
                u = entry.get("url")
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                api_responses.append(entry)
            record.apiResponses = api_responses[: cfg.max_api_responses]

        # optional image export (P2 multimodal pointer)
        if cfg.export_images and raw_html:
            img_urls = _extract_image_urls(url, raw_html)
            if img_urls:
                record.images = await _download_images(url, img_urls, _images_dir(cfg.export_images))

        if cfg.cache_dir:
            self._cache_write(url, record)
        return record
```

- [ ] **Step 5: Run the full suite (regression)**

```bash
uv run pytest tests/ -q
```

Expected: all pass (24 old + structured tests). The cache test (`test_scenario5_cache_no_reprocess`) must still pass — it now also exercises `fromCache` but the assertion set is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/growth_scraper/pipeline.py
git commit -m "refactor: ScrapeConfig per-run en Pipeline.run_one (concurrencia segura para server mode)"
```

---

## Task 4: `gscrape serve` — warm-browser HTTP server + endpoint tests

**Goal:** `gscrape serve` runs a FastAPI server on 127.0.0.1 with a warm crawler; `POST /scrape` returns a full record; cache fast-path and token support included.

**Files:**
- Create: `src/growth_scraper/server.py`
- Modify: `src/growth_scraper/cli.py:83` (`main` dispatch), `pyproject.toml:6-8` (deps)
- Test: `tests/test_server.py`

**Acceptance Criteria:**
- [ ] `gscrape serve [--port 8743] [--cache-dir DIR] [--max-concurrency N] [--token T] [--no-consent] [--no-expand] [--no-apis] [--ignore-robots]` dispatches from `gscrape` before URL parsing
- [ ] `GET /health` → `{"status":"ok","version":...}`
- [ ] `POST /scrape` → record JSON; `protectionBlocked`/`error` live inside the record (HTTP 200); invalid body / non-http URL → 400
- [ ] Cache fast-path: cached URL returns the record with `fromCache: true` and does not hit the network again
- [ ] Token: when configured, missing/wrong `Authorization: Bearer` → 401
- [ ] New deps (`fastapi`, `uvicorn`, `httpx`) declared and installed

**Verify:** `uv sync` succeeds; `uv run pytest tests/test_server.py -q` → all pass

**Steps:**

- [ ] **Step 1: Add the new dependencies**

In `pyproject.toml`, replace the `dependencies` block (lines 6-8) with:

```toml
dependencies = [
    "crawl4ai>=0.9",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "httpx>=0.27",
]
```

Then run:

```bash
uv sync
```

- [ ] **Step 2: Write the failing endpoint tests** in `tests/test_server.py`

```python
"""Tests for `gscrape serve` (warm-browser HTTP server)."""

from fastapi.testclient import TestClient
import pytest

from conftest import base_cfg
from growth_scraper.server import check_token, create_app


def _base_cfg():
    return base_cfg(page_timeout_ms=15000)


@pytest.fixture(scope="session")
def client():
    app = create_app(_base_cfg())
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["version"]


def test_scrape_ok(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/")})
    assert r.status_code == 200
    rec = r.json()
    assert rec["url"] == fs.url("/")
    assert "contenido principal" in rec["text"]
    assert rec["error"] is None


def test_scrape_protection_passthrough(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/blocked")})
    assert r.status_code == 200
    rec = r.json()
    assert rec["protectionBlocked"] is True
    assert rec["error"].startswith("PROTECTION_BLOCKED")
    assert rec["text"] == ""


def test_scrape_robots_record(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/private")})
    assert r.status_code == 200
    assert r.json()["error"].startswith("ROBOTS_BLOCKED")


def test_scrape_invalid_body(client):
    r = client.post("/scrape", json={})
    assert r.status_code == 400


def test_scrape_non_http_url(client):
    r = client.post("/scrape", json={"url": "ftp://example.com/x"})
    assert r.status_code == 400


def test_scrape_cache_fastpath(client, fs, tmp_path):
    cache_dir = str(tmp_path / "cache")
    before = fs.state.hits["/"]
    r1 = client.post("/scrape", json={"url": fs.url("/"),
                                      "options": {"cacheDir": cache_dir}})
    assert r1.status_code == 200
    assert r1.json().get("fromCache") is not True
    r2 = client.post("/scrape", json={"url": fs.url("/"),
                                      "options": {"cacheDir": cache_dir}})
    assert r2.status_code == 200
    assert r2.json()["fromCache"] is True
    assert fs.state.hits["/"] == before + 1


def test_scrape_options_respected(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/"),
                                     "options": {"maxTextChars": 40}})
    assert r.status_code == 200
    rec = r.json()
    assert rec["error"] is None
    assert 0 < len(rec["text"]) <= 40


def test_check_token_unit():
    check_token(None, "Bearer whatever")  # no token configured -> always ok
    check_token("secret", "Bearer secret")
    with pytest.raises(Exception):
        check_token("secret", "Bearer nope")
    with pytest.raises(Exception):
        check_token("secret", None)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_server.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'growth_scraper.server'`.

- [ ] **Step 4: Create `src/growth_scraper/server.py`**

```python
"""gscrape serve — warm-browser HTTP server (single-URL scrapes).

Binds to 127.0.0.1 only. Never expose this on a public interface: `/scrape`
fetches arbitrary URLs (SSRF surface).
"""

from __future__ import annotations

import asyncio
import copy
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import ScrapeConfig
from .pipeline import Pipeline
from .robots import RobotsPolicy

DEFAULT_PORT = 8743


class ScrapeOptions(BaseModel):
    maxTextChars: int | None = None
    fitText: bool = False
    ignoreRobots: bool = False
    noConsent: bool = False
    noExpand: bool = False
    noApis: bool = False
    maxRetries: int | None = None
    maxApiResponses: int | None = None
    cacheDir: str | None = None
    rawHtml: bool = False


class ScrapeRequest(BaseModel):
    url: str
    options: ScrapeOptions = Field(default_factory=ScrapeOptions)


def check_token(token: str | None, authorization: str | None) -> None:
    """Raise HTTPException 401 when the server is token-protected and the
    request does not carry the right bearer token."""
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid or missing token")


def create_app(base_cfg: ScrapeConfig, max_concurrency: int = 4, token: str | None = None) -> FastAPI:
    pipeline: Pipeline | None = None
    sem: asyncio.Semaphore | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal pipeline, sem
        robots = RobotsPolicy(cache_dir=base_cfg.cache_dir)
        pipeline = Pipeline(base_cfg, robots)
        await pipeline.start()
        sem = asyncio.Semaphore(max_concurrency)
        yield
        if pipeline is not None:
            await pipeline.close()

    app = FastAPI(title="gscrape serve", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content={"error": "invalid request", "detail": exc.errors()})

    @app.get("/health")
    async def health(authorization: str | None = Header(None)):
        check_token(token, authorization)
        return {"status": "ok", "version": __version__}

    @app.post("/scrape")
    async def scrape(req: ScrapeRequest, authorization: str | None = Header(None)):
        check_token(token, authorization)
        url = req.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must be http(s)")
        cfg = copy.copy(base_cfg)
        o = req.options
        if o.maxTextChars is not None:
            cfg.max_text_chars = o.maxTextChars
        cfg.fit_text = o.fitText
        cfg.ignore_robots = o.ignoreRobots
        cfg.handle_consent = not o.noConsent
        cfg.expand = not o.noExpand
        cfg.capture_apis = not o.noApis
        if o.maxRetries is not None:
            cfg.max_retries = o.maxRetries
        if o.maxApiResponses is not None:
            cfg.max_api_responses = o.maxApiResponses
        if o.cacheDir:
            cfg.cache_dir = o.cacheDir
        cfg.raw_html = o.rawHtml
        assert pipeline is not None and sem is not None
        try:
            async with sem:
                record = await pipeline.run_one(url, cfg=cfg)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return record.to_dict()

    return app


def serve_main(argv: list[str]) -> int:
    """`gscrape serve` CLI entrypoint (dispatched from cli.main)."""
    import argparse

    p = argparse.ArgumentParser(prog="gscrape serve")
    p.add_argument("--port", type=int, default=int(os.environ.get("GSCRAPE_PORT", DEFAULT_PORT)))
    p.add_argument("--cache-dir")
    p.add_argument("--max-concurrency", type=int, default=4)
    p.add_argument("--token")
    p.add_argument("--no-consent", action="store_true")
    p.add_argument("--no-expand", action="store_true")
    p.add_argument("--no-apis", action="store_true")
    p.add_argument("--ignore-robots", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    base_cfg = ScrapeConfig(
        cache_dir=args.cache_dir,
        handle_consent=not args.no_consent,
        expand=not args.no_expand,
        capture_apis=not args.no_apis,
        ignore_robots=args.ignore_robots,
        verbose=args.verbose,
    )
    app = create_app(base_cfg, max_concurrency=max(1, args.max_concurrency), token=args.token)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0
```

- [ ] **Step 5: Dispatch `serve` from the CLI**

In `cli.py`, replace the start of `main` (lines 83-84):

```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
```

with:

```python
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        from .server import serve_main

        return serve_main(argv[1:])
    args = build_parser().parse_args(argv)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_server.py -q
```

Expected: all pass.

- [ ] **Step 7: Run the whole suite**

```bash
uv run pytest tests/ -q
```

Expected: everything green (24 old + structured + server).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/growth_scraper/server.py src/growth_scraper/cli.py tests/test_server.py
git commit -m "feat: gscrape serve (server HTTP warm-browser, cache fast-path, token)"
```

---

## Task 5: Hybrid `orpheus.sh` (server first, spawn fallback) + scenario-server.sh

**Goal:** `orpheus.sh` tries `POST /scrape` on the local server first; if it's unreachable or invalid, it falls back to spawning `uv run gscrape`. The exit-0/text contract is unchanged.

**Files:**
- Modify: `~/.claude/scripts/orpheus.sh` (full rewrite)
- Create: `scripts/scenario-server.sh`

**Acceptance Criteria:**
- [ ] With a live server, `orpheus.sh <url> --max-chars N` returns text via HTTP, exit 0
- [ ] With the server down (or `GSCRAPE_SKIP_SERVER=1`), same text via spawn, exit 0
- [ ] `--out FILE` writes the JSONL record in both paths
- [ ] A blocked/error record (e.g. `/blocked`) → exit 1 + stderr message, NO spawn retry
- [ ] `--` extra flags force the spawn path
- [ ] `scripts/scenario-server.sh` validates warm path, cache path, and spawn fallback against a live URL

**Verify:** `bash scripts/scenario-server.sh` → PASS (requires internet)

**Steps:**

- [ ] **Step 1: Rewrite `~/.claude/scripts/orpheus.sh`**

```bash
#!/usr/bin/env bash
# orpheus.sh — wrapper universal sobre gscrape (growth-scraper / Orpheus).
#
# Scraper local, polite y LLM-ready. Coste = $0 (corre en tu máquina).
# PRIMERA OPCIÓN SIEMPRE: si no alcanza o lo bloquean, caer a Firecrawl.
#
# Ruta 1 (server mode): intenta `gscrape serve` en 127.0.0.1:$GSCRAPE_PORT.
# Ruta 2 (spawn): si no hay server, lanza `uv run gscrape` como antes.
#
# Uso:
#   orpheus.sh <url> [--max-chars N] [--out FILE] [-- GSCRAPE_FLAGS...]
#
#   --max-chars N      capa el texto LLM-ready (default 12000)
#   --out FILE         además del texto en stdout, guarda el record JSONL completo
#   -- <flags>         pasa flags verbatim a `uv run gscrape` (fuerza la ruta spawn)
#
# Contrato de salida:
#   exit 0 + texto limpio en stdout  -> scrape OK (usar el texto)
#   exit 1 + mensaje en stderr       -> bloqueado / robots / error / vacío
#                                     (caer a Firecrawl o al mecanismo actual)
#
# Env:
#   ORPHEUS_DIR         ruta al repo de growth-scraper (default ~/.claude/scripts/growth-scraper)
#   ORPHEUS_TIMEOUT_S   timeout global (default 90)
#   GSCRAPE_PORT        puerto del server mode (default 8743)
#   GSCRAPE_SKIP_SERVER 1 = saltarse la ruta server (fuerza spawn)
set -euo pipefail

ORPHEUS_DIR="${ORPHEUS_DIR:-$HOME/.claude/scripts/growth-scraper}"
ORPHEUS_TIMEOUT_S="${ORPHEUS_TIMEOUT_S:-90}"
GSCRAPE_PORT="${GSCRAPE_PORT:-8743}"
MAX_CHARS=12000
OUT_FILE=""
URL=""
PASS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-chars)
      MAX_CHARS="$2"; shift 2 ;;
    --out)
      OUT_FILE="$2"; shift 2 ;;
    --)
      shift
      PASS=("$@")
      break ;;
    -*)
      echo "orpheus.sh: flag desconocido $1 (usa '--' para pasar flags a gscrape)" >&2
      exit 2 ;;
    *)
      URL="$1"; shift ;;
  esac
done

if [[ -z "$URL" ]]; then
  echo "orpheus.sh: falta URL. Uso: orpheus.sh <url> [--max-chars N] [--out FILE] [-- GSCRAPE_FLAGS...]" >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "orpheus.sh: 'uv' no encontrado. Orpheus requiere uv (https://docs.astral.sh/uv)." >&2
  exit 1
fi
if [[ ! -d "$ORPHEUS_DIR" ]]; then
  echo "orpheus.sh: no existe $ORPHEUS_DIR (configura ORPHEUS_DIR)" >&2
  exit 1
fi

# --- tail: parsea UN record (JSON o primera línea de un JSONL) y emite el texto
emit_record() {
  python3 - "$1" <<'PY'
import json, sys
line = open(sys.argv[1], encoding="utf-8").readline()
try:
    rec = json.loads(line)
except Exception:
    sys.exit(1)
if not isinstance(rec, dict) or "url" not in rec:
    sys.exit(1)
if rec.get("protectionBlocked") or rec.get("error"):
    err = rec.get("error") or "protection blocked"
    print(f"orpheus.sh: {err[:160]}. Caer a Firecrawl.", file=sys.stderr)
    sys.exit(1)
text = (rec.get("text") or "").strip()
if not text:
    print("orpheus.sh: texto vacío. Caer a Firecrawl.", file=sys.stderr)
    sys.exit(1)
print(text)
PY
}

# --- Ruta 1: server mode (solo si no hay flags extra que el server no soporta)
if [[ -z "${GSCRAPE_SKIP_SERVER:-}" && ${#PASS[@]} -eq 0 ]]; then
  SRV_TMP="$(mktemp "${TMPDIR:-/tmp}/orpheus-srv-XXXXXX.json")"
  BODY="$(python3 - "$URL" "$MAX_CHARS" <<'PY'
import json, sys
print(json.dumps({"url": sys.argv[1], "options": {"maxTextChars": int(sys.argv[2]), "delay": 0.3, "jitter": 0.15}}))
PY
)"
  if curl -sS --max-time "$ORPHEUS_TIMEOUT_S" -H 'Content-Type: application/json' \
      -X POST -d "$BODY" "http://127.0.0.1:${GSCRAPE_PORT}/scrape" \
      -o "$SRV_TMP" 2>/dev/null; then
    if python3 - "$SRV_TMP" <<'PY'
import json, sys
try:
    rec = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(rec, dict) and "url" in rec else 1)
PY
    then
      if [[ -n "$OUT_FILE" ]]; then
        cp "$SRV_TMP" "$OUT_FILE"
        printf '\n' >> "$OUT_FILE"
      fi
      if emit_record "$SRV_TMP"; then
        status=0
      else
        status=$?
      fi
      rm -f "$SRV_TMP"
      exit "$status"
    fi
  fi
  rm -f "$SRV_TMP"
fi

# --- Ruta 2: spawn (ruta clásica)
TMP="$(mktemp "${TMPDIR:-/tmp}/orpheus-XXXXXX.jsonl")"
ERRLOG="$(mktemp "${TMPDIR:-/tmp}/orpheus-XXXXXX.err")"
cleanup() { rm -f "$TMP" "$ERRLOG"; }
trap cleanup EXIT

(
  cd "$ORPHEUS_DIR"
  uv run gscrape "$URL" -o "$TMP" --max-text-chars "$MAX_CHARS" --delay 0.3 --jitter 0.15 ${PASS[@]+"${PASS[@]}"} >"$ERRLOG" 2>&1
) &
pid=$!
( sleep "$ORPHEUS_TIMEOUT_S" && kill -TERM "$pid" 2>/dev/null ) &
killer=$!
wait "$pid" 2>/dev/null
status=$?
kill "$killer" 2>/dev/null
wait "$killer" 2>/dev/null || true

if [[ $status -ne 0 ]]; then
  echo "orpheus.sh: gscrape falló (exit $status): $(head -c 200 "$ERRLOG" | tr '\n' ' ')" >&2
  exit 1
fi

read -r line <"$TMP" || true
if [[ -z "$line" ]]; then
  echo "orpheus.sh: sin records — Orpheus no pudo leer $URL (probable anti-bot o robots.txt). Caer a Firecrawl." >&2
  exit 1
fi

[[ -n "$OUT_FILE" ]] && cp "$TMP" "$OUT_FILE"
if emit_record "$TMP"; then
  exit 0
else
  exit $?
fi
```

Make it executable:

```bash
chmod +x "$HOME/.claude/scripts/orpheus.sh"
```

- [ ] **Step 2: Create `scripts/scenario-server.sh`**

```bash
#!/usr/bin/env bash
# Scenario 6 — server mode: warm path, cache path, fallback a spawn.
set -uo pipefail
cd "$(dirname "$0")/.."

ORPHEUS_SH="${ORPHEUS_SH:-$HOME/.claude/scripts/orpheus.sh}"
PORT="${GSCRAPE_PORT:-8799}"
URL="${1:-https://www.python.org}"
CACHE="work/scenario6-cache"

mkdir -p work

echo "==> server: gscrape serve --port $PORT --cache-dir $CACHE"
uv run gscrape serve --port "$PORT" --cache-dir "$CACHE" &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT
sleep 4

echo "==> warm path (server arriba)"
OUT="$(GSCRAPE_PORT="$PORT" "$ORPHEUS_SH" --max-chars 800 "$URL")" || { echo "FAIL: warm path exit != 0"; exit 1; }
[[ -n "$OUT" ]] || { echo "FAIL: warm path texto vacío"; exit 1; }
echo "    warm text chars: ${#OUT}"

echo "==> cache path (2ª llamada al server)"
OUT2="$(GSCRAPE_PORT="$PORT" "$ORPHEUS_SH" --max-chars 800 "$URL")" || { echo "FAIL: cache path exit != 0"; exit 1; }
[[ "$OUT2" == "$OUT" ]] || { echo "FAIL: cache path distinto del warm"; exit 1; }

echo "==> fallback a spawn (server muerto)"
kill "$SRV"
wait "$SRV" 2>/dev/null || true
trap - EXIT
OUT3="$(GSCRAPE_SKIP_SERVER=1 "$ORPHEUS_SH" --max-chars 800 "$URL")" || { echo "FAIL: spawn path exit != 0"; exit 1; }
[[ "$OUT3" == "$OUT" ]] || { echo "FAIL: spawn path distinto del server"; exit 1; }

echo "==> scenario 6 PASS"
```

Make it executable:

```bash
chmod +x scripts/scenario-server.sh
```

- [ ] **Step 3: Verify the offline suite still passes (wrapper change is shell-only, but confirm)**

```bash
uv run pytest tests/ -q
```

Expected: all green.

- [ ] **Step 4: Verify the live scenario**

```bash
bash scripts/scenario-server.sh
```

Expected: `==> scenario 6 PASS` (needs internet + Chromium installed).

- [ ] **Step 5: Commit**

```bash
git add scripts/scenario-server.sh
git commit -m "feat: orpheus.sh híbrido (server mode first, spawn fallback) + escenario 6"
```

Note: `orpheus.sh` lives outside this repo (`~/.claude/scripts/`), so it is NOT committed here; it is updated directly on disk.

---

## Task 6: Docs (README + AGENTS) + final verification

**Goal:** Document `serve`, `structured`, and the hybrid wrapper; run the full verification battery.

**Files:**
- Modify: `README.md`, `AGENTS.md`

**Acceptance Criteria:**
- [ ] README documents `gscrape serve`, the `structured` field, and the wrapper behavior
- [ ] AGENTS.md has the new module rows, decisions D25-D29, and updated test counts/evidence
- [ ] Full verification battery passes

**Verify:** `uv run pytest tests/ -q`, `bash scripts/fixture-check.sh`, and (live) `bash scripts/scenario-server.sh` → all PASS

**Steps:**

- [ ] **Step 1: Update `README.md`**

Add a "Server mode" section after the "Quick start" block:

```markdown
## Server mode (warm browser)

For consumers that scrape many URLs in a row (the TS wrappers via `orpheus.sh`),
run a persistent server instead of spawning a cold process per URL:

```bash
uv run gscrape serve                # 127.0.0.1:8743
uv run gscrape serve --port 9000 --cache-dir .cache --token secret
```

- `GET /health` → `{"status": "ok", "version": "..."}`
- `POST /scrape` → `{"url": "...", "options": {...}}` → the same record shape as the CLI.
  Options: `maxTextChars`, `fitText`, `ignoreRobots`, `noConsent`, `noExpand`, `noApis`,
  `maxRetries`, `maxApiResponses`, `cacheDir`, `rawHtml`.
- With `--cache-dir` (server or per-request `cacheDir`), a cached URL returns
  immediately with `"fromCache": true`.
- Optional `--token` (send `Authorization: Bearer <token>`). Binds to 127.0.0.1 only —
  do not expose publicly (SSRF).

The wrapper `orpheus.sh` uses the server automatically and falls back to spawning
`uv run gscrape` when the server is not running (set `GSCRAPE_SKIP_SERVER=1` to force
the spawn path). The exit-0/text contract is unchanged.
```

Add a "Structured data" paragraph near the record example:

```markdown
Records also carry a `structured` field (best-effort, fail-open) extracted from
schema.org JSON-LD, microdata, meta/OG, or heuristic selectors:

```json
"structured": {
  "entityType": "profile",
  "source": "jsonld",
  "name": "Fotografía Luna",
  "price": {"value": "€€", "currency": null, "isRange": true},
  "rating": {"value": 4.9, "best": 5, "count": 127},
  "reviews": [{"author": "María", "rating": 5, "text": "..."}],
  "category": null,
  "contact": {"phone": "...", "email": "...", "website": "...", "address": {...}},
  "itemCount": null
}
```

`summary` mirrors the cheap triage fields (`structuredSource`, `structuredPrice`,
`structuredRatingValue`, `structuredReviewCount`, `structuredCategory`) and `--csv`
adds them as columns.
```

- [ ] **Step 2: Update `AGENTS.md`**

Add to the architecture table:

```
| `server.py` | FastAPI + uvicorn: `gscrape serve`, single warm `AsyncWebCrawler`, `POST /scrape`, cache fast-path, `--token`. Binds 127.0.0.1. |
| `structured.py` | P2: entidades estructuradas (JSON-LD → microdata → meta/OG → heurística), fail-open, campo `structured` + triage en `summary`/CSV. |
```

Add decisions D25-D29 after D24:

```
- **D25 — uvicorn es dueño del event loop.** El server no llama `asyncio.run` por
  request; el crawler se crea/cierra en el lifespan de la app (compatible con D7).
- **D26 — Sesión por request** (D18) en server mode. Cada `POST /scrape` crea su
  `Session` y hace `kill_session` al terminar, aunque falle.
- **D27 — 200 siempre que el scrape corra.** `protectionBlocked`/`error` viven en el
  record (igual que CLI). Errores de contrato/transporte → 400 (body inválido) o 500.
- **D28 — `structured` fail-open.** Cualquier error o HTML sin señales → `structured:
  null`. Nunca rompe el record. `source` = primera fuente con ≥1 campo (jsonld >
  microdata > meta > heuristic).
- **D29 — Server single-URL y solo loopback.** SSRF documentado; `--token` opcional.
  `orpheus.sh` intenta el server primero y cae a spawn si no responde.
```

Update the test count and evidence section:

```
`uv run pytest tests/ -q` → **24 + structured + server tests en verde**.

### Validación live (server mode)

- `scripts/scenario-server.sh` (python.org) → warm path, cache path y fallback a
  spawn devuelven el mismo texto. Verificado live.
```

- [ ] **Step 3: Run the full verification battery**

```bash
uv run pytest tests/ -q
bash scripts/fixture-check.sh
```

Expected: all tests pass; all 5 fixture scenarios PASS. If internet is available, also:

```bash
bash scripts/scenario-server.sh
```

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: server mode, structured, wrapper híbrido (escenario 6)"
```

---

## Self-review summary

- **Spec coverage:** §5.1 → Task 4; §5.2 → Task 5; §5.3 → Tasks 1-2; §5.4 → Task 1
  (config); §5.5 → Tasks 1-2 (fixtures + unit/e2e) + Task 4 (server tests) + Task 5
  (scenario); §6 → Task 6. Non-goals (§3) untouched.
- **Type consistency:** `extract_structured(html, summary=None) -> dict|None` used in
  Task 2/3 as `extract_structured(raw_html)`; `Record.structured`/`Record.fromCache`
  match `to_dict`; `run_one(url, crawled_from=None, cfg=None)` matches the server call
  `pipeline.run_one(url, cfg=cfg)`; `check_token` matches the tests. The `--cache-dir`
  per-request option (`options.cacheDir`) maps to `cfg.cache_dir`; robots cache stays at
  server level (documented in spec).
- **Placeholders:** none — every step ships complete code.
