"""URL normalization for dedupe and cache keys.

Strips marketing/session tracking params (utm_*, fbclid, gclid, ...) and
normalizes the host so the same page reached via different tracking links
counts as one URL. Keeps everything else (path + meaningful params) intact.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query params that are tracking/session noise, never page content.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid",
    "gbraid", "wbraid", "epik", "ttclid", "twclid", "li_fat_id",
    "mc_cid", "mc_eid", "_ga", "_gl", "_gid", "_gat",
    "ref_src", "cmpid", "session_id", "sessionid", "sess_id", "sessid",
}


def is_tracking_param(key: str) -> bool:
    key = key.lower()
    if key in TRACKING_PARAMS:
        return True
    return key.startswith("utm_")


def normalize_url(url: str) -> str:
    """Canonical form for dedupe: lowercase host, no fragment, no tracking params."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url
    if parsed.scheme not in ("http", "https"):
        return url
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not is_tracking_param(k)]
    netloc = parsed.netloc.lower()
    return urlunparse(parsed._replace(netloc=netloc, fragment="", query=urlencode(query, doseq=True)))
