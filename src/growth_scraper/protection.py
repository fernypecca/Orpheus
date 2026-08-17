"""Fail-closed anti-bot detection.

If a site defends itself (Cloudflare challenge, DataDome, Akamai, Incapsula...)
we do NOT try to bypass it. We detect it and fail loudly and clearly. This
module inspects a Crawl4AI result and returns a human reason, or None.

Important: being behind a CDN (cf-ray / x-amz-cf-id / server: cloudflare) is
NOT a block signal by itself — most big sites use Cloudflare/CloudFront as a
CDN and serve perfectly fine. We only fail when there is *evidence* of an
actual challenge or bot-mitigation block (403/429 + anti-bot server, challenge
page title, or challenge HTML shell).
"""

from __future__ import annotations

from .config import PROTECTED_HTML_FRAGMENTS, PROTECTED_TITLE_FRAGMENTS

_TITLE_HINTS = [t.lower() for t in PROTECTED_TITLE_FRAGMENTS]
_HTML_HINTS = [h.lower() for h in PROTECTED_HTML_FRAGMENTS]
_ANTIBOT_SERVERS = ("cloudflare", "akamai", "incapsula", "ddos-guard")
_DATADOME_PASSED = ("passed", "hit", "ok", "")


def detect(result) -> str | None:
    """Return a protection reason string, or None if the page looks normal."""
    headers = result.response_headers or {}
    html = (result.html or "").lower()
    title = ""
    if result.metadata:
        title = (result.metadata.get("title") or "").lower()
    status = getattr(result, "status_code", None)
    server = (headers.get("server") or headers.get("Server") or "").lower()
    datadome = (headers.get("x-datadome") or headers.get("X-Datadome") or "").lower()

    # 1) Blocking status from an anti-bot server is the strongest signal.
    if status in (403, 429):
        if any(h in server for h in _ANTIBOT_SERVERS):
            return f"PROTECTION_BLOCKED: HTTP {status} from {server} (bot mitigation)"
        if datadome and datadome not in _DATADOME_PASSED:
            return "PROTECTION_BLOCKED: HTTP {0} + DataDome (x-datadome)".format(status)

    # 2) Challenge-page titles (some blocks return HTTP 200).
    for frag in _TITLE_HINTS:
        if frag in title:
            return f'PROTECTION_BLOCKED: challenge page detected (title contains "{frag}")'

    # 3) Challenge HTML shells / anti-bot markers in the payload.
    for frag in _HTML_HINTS:
        if frag in html:
            return f'PROTECTION_BLOCKED: anti-bot marker "{frag}" found in HTML'

    # 4) DataDome sometimes serves a genuine challenge even on HTTP 200
    #    (soft-block, no 403). But the header value alone is not enough: sites
    #    also stamp "x-datadome: protected" on fully-served, real pages (e.g.
    #    bodas.net vendor profiles return 200 with the real page and this
    #    exact header — verified live). A real challenge/captcha shell is
    #    short; a full rendered page is not. Only trust the header when the
    #    payload also looks like a short challenge, not real content.
    if datadome and datadome not in _DATADOME_PASSED and len(html) < 5000:
        return f"PROTECTION_BLOCKED: DataDome (x-datadome: {datadome}, short challenge payload)"

    # 5) No HTTP status AND no payload at all: a challenge that never resolved
    #    (e.g. demo.datadome.co served us exactly status=None, empty title,
    #    empty text). Fail closed instead of silently emitting an empty record.
    if status is None and not html and not title:
        return "PROTECTION_BLOCKED: no HTTP status and empty payload (bot challenge / stalled load)"

    return None