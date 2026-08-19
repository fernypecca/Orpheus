"""Full-page screenshot capture (CLI-only multimodal pointer).

CLI writes PNGs to a directory and the record carries the paths; the server
never writes to disk (same rationale as --export-images).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from urllib.parse import urlparse


async def capture_screenshot(page, url: str, out_dir: str) -> str | None:
    """Save a full-page PNG into out_dir. Returns the absolute path or None."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        host = (urlparse(url).hostname or "unknown").replace(".", "_")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha1(url.encode()).hexdigest()[:8]
        path = os.path.join(out_dir, f"{host}-{ts}-{digest}.png")
        await page.screenshot(path=path, full_page=True)
        return os.path.abspath(path)
    except Exception:
        return None