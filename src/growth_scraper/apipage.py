"""P1: replay internal API calls with pagination to fetch more than the page loads.

When a captured API response looks like a list (`items/results/data/...`) and its
URL carries pagination params (page/offset/limit/...), we replay the SAME call
inside the browser (same origin -> same cookies, same look to the server) with
incremented page values, up to a cap. No fingerprint evasion: we are just using
the site's own public GET endpoint a few more times.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .config import MAX_PAGINATION_PAGES

_PAGINATION_KEYS = {
    "page", "pagenum", "page_number", "pagenumber", "pageindex",
    "offset", "start", "from",
    "limit", "per_page", "perpage", "pagesize", "page_size", "count", "size",
}
_LIST_KEYS = ("items", "results", "data", "list", "rows", "entries", "docs", "hits")


def _list_from_body(body) -> list | None:
    """Extract a list from a JSON body, or None if it isn't list-like."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in _LIST_KEYS:
            if isinstance(body.get(key), list):
                return body[key]
    return None


def _paginated(url: str) -> tuple[str, str] | None:
    """Return (param_key, current_value) if the URL has a pagination param."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in _PAGINATION_KEYS:
        if key in qs and qs[key]:
            return key, qs[key][0]
    return None


def _bump(url: str, key: str, value) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    qs[key] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


_FETCH_JS = """
async function (url) {
  try {
    var r = await fetch(url, {headers: {'Accept': 'application/json'}});
    if (!r.ok) return null;
    var t = await r.text();
    return JSON.parse(t);
  } catch (e) { return null; }
}
"""


async def replay_pagination(page, page_url: str, api_responses: list[dict], max_pages: int = MAX_PAGINATION_PAGES) -> list[dict]:
    """Replay paginated GET endpoints captured for this page. Returns new entries."""
    out: list[dict] = []
    seen_urls = {r["url"] for r in api_responses}
    added: dict[str, dict] = {}

    for resp in api_responses:
        url = resp.get("url", "")
        body = resp.get("body")
        if not isinstance(url, str) or body is None:
            continue
        pag = _paginated(url)
        items = _list_from_body(body)
        if pag is None or items is None:
            continue
        key, start = pag
        try:
            current = int(start)
        except ValueError:
            continue
        if current > 1:  # already at page N: keep going forward
            pass
        for _ in range(max_pages):
            current += 1
            next_url = _bump(url, key, current)
            if next_url in seen_urls or next_url in added:
                break
            try:
                new_body = await page.evaluate(f"({_FETCH_JS})('{next_url}')")
            except Exception:
                break
            if new_body is None:
                break
            new_items = _list_from_body(new_body)
            if not new_items:
                break
            # stop if nothing new (end of pagination)
            if all(x in items for x in new_items) and len(items) >= len(new_items):
                break
            items = list(items) + [x for x in new_items if x not in items]
            added[next_url] = {"url": next_url, "body": new_body}
            if len(items) > 100:
                break

    out = list(added.values())
    return out