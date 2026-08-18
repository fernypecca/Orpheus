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
    if value is None:
        price_range = offers.get("priceRange") or ""
        if price_range:
            return {"value": _clean(price_range), "currency": _clean(currency), "isRange": True}
        if high:
            return {"value": _clean(high), "currency": _clean(currency), "isRange": True}
        return None
    is_range = bool(high) or (isinstance(value, str) and "-" in value or "–" in value)
    if high:
        value = f"{value}–{high}"
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
    itemtype = scope.get("itemtype", "").lower()
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
    price = props.get("price")
    price_range = props.get("priceRange")
    if price or price_range:
        value = price or price_range
        out["price"] = {"value": value, "currency": props.get("priceCurrency") or None,
                        "isRange": bool(price_range) or "-" in value or "–" in value}
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
        out["price"] = {"value": price, "currency": currency or None, "isRange": "-" in price or "–" in price}
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
        out["price"] = {"value": price, "currency": None, "isRange": "-" in price or "–" in price}
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

def extract_structured(html: str) -> dict | None:
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