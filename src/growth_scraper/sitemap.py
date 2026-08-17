"""Sitemap seed discovery (--sitemap).

Growth research on marketplaces/directories: most of the interesting pages
(provider, product, venue profiles) are only reachable via the site's own
sitemap. We fetch sitemap.xml (or a sitemap index) and use every <loc> as a
seed URL, filtered by robots.txt and our URL keepability rules.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx

from .config import ROBOTS_UA_TOKEN, SITEMAP_MAX_URLS
from .urlutil import normalize_url

_DEPTH_CAP = 3          # sitemap -> index -> sitemap nesting
_TIMEOUT = 20
_MAX_SITEMAP_BYTES = 10_000_000


def _fetch(url: str) -> str:
    with httpx.Client(
        follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": ROBOTS_UA_TOKEN}
    ) as client:
        resp = client.get(url)
    if resp.status_code >= 400:
        return ""
    return resp.text[: _MAX_SITEMAP_BYTES]


def _locs_from(xml_body: str) -> list[str]:
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError:
        return []
    out: list[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
        if tag == "loc" and el.text and el.text.strip():
            out.append(el.text.strip())
    return out


async def fetch_sitemap_urls(
    sitemap_url: str,
    robots,
    max_urls: int = SITEMAP_MAX_URLS,
) -> list[str]:
    """Fetch a sitemap (or index) and return usable, robots-allowed URLs.

    `sitemap_url` can be an explicit sitemap URL or a site URL whose
    /sitemap.xml we try automatically.
    """
    base = _normalize_sitemap_url(sitemap_url)
    queue = [base]
    out: list[str] = []
    seen: set[str] = set()

    for _depth in range(_DEPTH_CAP):
        if not queue or len(out) >= max_urls:
            break
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        body = await asyncio.to_thread(_fetch, current)
        if not body:
            continue
        locs = _locs_from(body)
        if "<sitemapindex" in body[:512]:
            queue.extend(locs)
            continue
        for loc in locs:
            if len(out) >= max_urls:
                break
            loc = normalize_url(loc)
            if not _usable(loc):
                continue
            if not robots or await robots.is_allowed(loc):
                out.append(loc)
    return out


def _usable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.fragment:
        return False
    return True


def _normalize_sitemap_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path.rstrip("/").endswith(".xml"):
        return url
    return urljoin(url, "/sitemap.xml")