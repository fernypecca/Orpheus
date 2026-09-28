"""Meta Ad Library via the official API (graph.facebook.com/ads_archive).

The legitimate route to Meta ads data: structured JSON, no scraping, no login
walls, no anti-bot roulette. Requirements live with the USER (Meta developer
account + identity verification + app review for the Ad Library API); this
module only spends a token the user provides.

Coverage nuance (verified against Meta docs, API v26, sep-2026):
- political / social-issue ads: worldwide.
- ALL ad types: EU + UK only (DSA). Spain searches return commercial ads.
- commercial ads outside EU/UK: NOT available via this API.

Output matches the Orpheus `ads` card shape (see ads.py):
  {library: "meta", advertiser, text, creative_url, start/end, platforms, id}
plus page-level fields the API reliably returns. Impressions/spend exist only
for political/EU rows and are intentionally left out rather than half-filled.

Rate limits (error 613) get a modest backoff (5s, 20s) then an honest error.
The token NEVER appears in errors, logs, or records.
"""

from __future__ import annotations

import time

GRAPH_VERSION = "v26.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}/ads_archive"
_PAGE_SIZE = 25
_MAX_PAGES = 40  # 25 x 40 = 1000 ads hard cap per call

_FIELDS = ",".join([
    "id", "page_id", "page_name", "ad_snapshot_url",
    "ad_creative_bodies", "ad_creative_link_titles",
    "ad_creative_link_descriptions", "ad_creative_link_captions",
    "ad_delivery_start_time", "ad_delivery_stop_time",
    "publisher_platforms", "currency",
])

_AD_TYPES = ("ALL", "POLITICAL_AND_ISSUE_ADS", "HOUSING_ADS",
             "EMPLOYMENT_ADS", "FINANCIAL_PRODUCTS_AND_SERVICES_ADS")


def _clean(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text or "").strip()


def _normalize(item: dict) -> dict:
    bodies = item.get("ad_creative_bodies") or []
    titles = item.get("ad_creative_link_titles") or []
    descs = item.get("ad_creative_link_descriptions") or []
    caps = item.get("ad_creative_link_captions") or []
    parts = [_clean(t) for t in bodies + titles + descs if _clean(t)]
    captions = [_clean(c) for c in caps if _clean(c)]
    return {
        "library": "meta",
        "advertiser": _clean(item.get("page_name")) or None,
        "page_id": str(item.get("page_id") or "") or None,
        "text": "\n".join(parts) or None,
        "caption": captions[0] if captions else None,
        "creative_url": item.get("ad_snapshot_url") or None,
        "image_url": None,
        "format": None,
        "start": item.get("ad_delivery_start_time"),
        "end": item.get("ad_delivery_stop_time"),
        "platforms": item.get("publisher_platforms") or None,
        "currency": item.get("currency") or None,
        "id": f"meta:{item.get('id')}" if item.get("id") else None,
    }


def fetch_meta_ads(
    *,
    token: str,
    search_terms: str = "",
    countries: list[str] | None = None,
    ad_type: str = "ALL",
    page_ids: list[str] | None = None,
    limit: int = 50,
    timeout_s: float = 30.0,
    _client=None,
) -> tuple[list[dict], str | None]:
    """(ads, error). error is None on success (even with 0 results).
    Never raises, never leaks the token."""
    if not token:
        return [], "missing token (META_ADS_TOKEN or --token)"
    if ad_type not in _AD_TYPES:
        return [], f"invalid ad_type (choose from {', '.join(_AD_TYPES)})"
    countries = [c.upper() for c in (countries or ["ES"]) if c]
    if not countries:
        return [], "at least one country is required"

    import httpx

    client = _client or httpx.Client(timeout=timeout_s)
    close = _client is None
    try:
        params: dict = {
            "search_terms": search_terms or "",
            "ad_reached_countries": str(countries).replace("'", '"'),
            "ad_type": ad_type,
            "fields": _FIELDS,
            "limit": min(_PAGE_SIZE, max(1, limit)),
        }
        if page_ids:
            params["search_page_ids"] = ",".join(page_ids[:10])
        ads: list[dict] = []
        pages = 0
        url: str | None = None
        while len(ads) < limit and pages < _MAX_PAGES:
            pages += 1
            data, error = _get_page(client, url or GRAPH_URL, params, token)
            if error:
                return ads, error
            params = {}  # cursor URL already carries everything
            for item in data.get("data", []):
                if isinstance(item, dict):
                    ads.append(_normalize(item))
                    if len(ads) >= limit:
                        break
            paging = data.get("paging") or {}
            cursors = paging.get("cursors") or {}
            url = paging.get("next")
            if not url or not cursors.get("after"):
                break
        return ads, None
    except Exception:
        return [], "request failed"
    finally:
        if close:
            try:
                client.close()
            except Exception:
                pass


def _get_page(client, url: str, params: dict, token: str, retries: int = 2):
    """One GET with modest backoff on rate-limit (613). Returns (data, error)."""
    wait_s = 5.0
    for attempt in range(retries + 1):
        try:
            all_params = dict(params)
            all_params["access_token"] = token
            resp = client.get(url, params=all_params)
        except Exception:
            if attempt < retries:
                time.sleep(wait_s)
                wait_s *= 4
                continue
            return {}, "network error calling the Ad Library API"
        if resp.status_code == 200:
            try:
                return resp.json(), None
            except Exception:
                return {}, "invalid JSON from the Ad Library API"
        try:
            body = resp.json().get("error", {})
            code = body.get("code")
        except Exception:
            code, body = None, {}
        if code == 613 and attempt < retries:  # rate limited
            time.sleep(wait_s)
            wait_s *= 4
            continue
        if code in (100, 1009, 2500):
            return {}, "invalid parameters (check countries/ad_type/page_ids)"
        if code == 190:
            return {}, "invalid or expired token"
        return {}, f"Ad Library API error (HTTP {resp.status_code})"
    return {}, "rate limited, try again later"
