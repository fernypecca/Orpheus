"""P0: network recorder.

Attaches Playwright request/response listeners to a page so we capture BOTH
outgoing requests (to understand what a button/site fires) and response bodies
(the JSON APIs the page calls internally). This is the tool's answer to:
"clicking the button doesn't always reveal content — what exactly triggers it?"

Only JSON responses from the same domain (or a subdomain of it) are surfaced as
`apiResponses`; everything else is kept in the trace for verbose debugging.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from .config import MAX_API_BODY_BYTES

_ANALYTICS_URL_HINTS = (
    "/pixel", "/collect", "/beacon", "/analytics", "/track", "/telemetry",
    "amplitude", "segment.io", "mixpanel", "posthog", "heapanalytics",
    "googletagmanager", "google-analytics", "ga4", "hotjar", "mouseflow",
    "/metrics", "sentry",
)

_TRIVIAL_BODIES = (
    "{}", "[]", '{"ok":true}', '{"success":true}', '{"status":"ok"}',
)


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _same_origin(page_url: str, other_url: str) -> bool:
    page_host = _host_of(page_url).removeprefix("www.")
    other_host = _host_of(other_url)
    if not other_host:
        return False
    return other_host == page_host or other_host.endswith("." + page_host)


class NetworkRecorder:
    def __init__(self):
        self.page_url = ""
        self.events: list[dict] = []
        self._api: dict[str, dict] = {}  # url -> {"url","body"}
        self._active = False

    # -- lifecycle -----------------------------------------------------------
    def reset(self, page_url: str) -> None:
        self.page_url = page_url
        self.events.clear()
        self._api.clear()
        self._active = True

    def snapshot(self) -> list[dict]:
        return list(self._api.values())

    def count(self) -> int:
        return len(self._api)

    def deactivate(self) -> None:
        self._active = False

    def trace(self) -> list[dict]:
        return self.events

    # -- playright wiring (called from the on_page_context_created hook) -----
    async def attach(self, page) -> None:
        async def on_request(req):
            if not self._active:
                return
            body = None
            try:
                body = req.post_data
            except Exception:
                pass
            self.events.append({
                "event": "request",
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "post_data": body,
                "timestamp": __import__("time").time(),
            })

        async def on_response(resp):
            if not self._active:
                return
            ct = (resp.headers.get("content-type", "") or "").lower()
            if "json" not in ct:
                return
            url = resp.url
            if not _same_origin(self.page_url, url):
                return
            if any(h in url.lower() for h in _ANALYTICS_URL_HINTS):
                return
            if resp.status not in (200, 201):
                return
            try:
                clen = int(resp.headers.get("content-length", "0") or "0")
                if clen > MAX_API_BODY_BYTES:
                    return
                body = await resp.json()
            except Exception:
                return
            if isinstance(body, str):
                body = body.strip()
            serialized = json.dumps(body, ensure_ascii=False, default=str)
            if serialized in _TRIVIAL_BODIES:
                return
            if len(serialized) > MAX_API_BODY_BYTES:
                return
            self._api[url] = {"url": url, "body": body}

        page.on("request", on_request)
        page.on("response", on_response)