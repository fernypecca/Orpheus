"""Bounded wait for gated content after a consent dismissal.

Some SPAs only load real content into the DOM once the consent wall is
dismissed. After a reject/hide action we wait (bounded) for the body text to
stabilize so extraction sees the complete page. Fail-open: never raises.
"""

from __future__ import annotations

import asyncio
import time

_POLL_MS = 500
_PLATEAU_RATIO = 0.05
_PLATEAU_POLLS = 2


async def _text_len(page) -> int:
    try:
        return int(await page.evaluate("document.body.innerText.length"))
    except Exception:
        return -1


async def wait_for_content(page, cfg) -> str:
    """Wait until body text stops growing, capped at cfg.consent_wait_ms."""
    if not cfg.consent_wait_ms:
        return "off"
    start = time.monotonic()
    prev = await _text_len(page)
    stable = 0
    while time.monotonic() - start < cfg.consent_wait_ms / 1000:
        await asyncio.sleep(_POLL_MS / 1000)
        cur = await _text_len(page)
        if prev >= 0 and cur >= 0:
            growth = (cur - prev) / max(prev, 1)
            if growth < _PLATEAU_RATIO:
                stable += 1
                if stable >= _PLATEAU_POLLS:
                    return f"wait {time.monotonic() - start:.1f}s delta {cur - prev:+d}"
            else:
                stable = 0
        prev = cur
    return f"wait timeout {cfg.consent_wait_ms}ms len {prev}"


def needs_wait(consent_status: str) -> bool:
    """True only when the consent handler actually did something."""
    if not consent_status:
        return False
    if consent_status.startswith(("no-consent", "error")):
        return False
    return True