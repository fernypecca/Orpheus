"""Crawl mode: follow same-domain links with a page cap.

Concurrent workers process pages in parallel (--concurrency) while staying
polite: robots.txt is still respected, and per-domain crawl-delay + base delay
are enforced as a minimum gap between two requests to the same host.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from .config import ScrapeConfig
from .pipeline import Pipeline
from .records import JsonlWriter, emit_progress
from .urlutil import normalize_url

_SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".zip", ".gz", ".tar", ".mp4", ".webm", ".mp3", ".mov",
    ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)


def base_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def in_domain(url: str, base: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == base or host.endswith("." + base)


def _keepable(link_url: str) -> bool:
    parsed = urlparse(link_url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.fragment:
        return False
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in _SKIP_EXTENSIONS):
        return False
    return True


class _CrawlState:
    """Shared mutable crawl state (single-threaded asyncio -> no data races)."""

    def __init__(self, cfg: ScrapeConfig, seeds: list[str], domains: set[str]):
        self.cfg = cfg
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.pending = 0
        self.visited: set[str] = set()
        self.processed = 0
        self.lock = asyncio.Lock()
        self.domains = domains
        self.last_request: dict[str, float] = {}
        for s in seeds:
            self.pending += 1
            self.queue.put_nowait(s)

    async def try_claim(self) -> str | None:
        """Pop the next URL, or None when the crawl is over."""
        while True:
            async with self.lock:
                if self.pending == 0:
                    return None
                self.pending -= 1
            try:
                return await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # another worker claimed the last one; re-check
                async with self.lock:
                    if self.pending == 0:
                        return None

    async def schedule(self, url: str) -> None:
        async with self.lock:
            self.pending += 1
            self.queue.put_nowait(url)

    async def finish(self) -> None:
        self.queue.task_done()

    async def pace(self, url: str, crawl_delay: float | None) -> None:
        """Minimum gap between two requests to the same host (robots wins)."""
        domain = urlparse(url).netloc
        min_gap = self.cfg.polite_delay(crawl_delay)
        async with self.lock:
            now = time.monotonic()
            wait = min_gap - (now - self.last_request.get(domain, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_request[domain] = time.monotonic()


async def crawl_seeds(pipeline: Pipeline, seeds: list[str], cfg: ScrapeConfig, writer: JsonlWriter) -> None:
    seeds = [normalize_url(s) for s in seeds]
    domains = {base_host(urlparse(s).hostname or "") for s in seeds}
    state = _CrawlState(cfg, seeds, domains)

    async def worker() -> None:
        while True:
            url = await state.try_claim()
            if url is None:
                return
            try:
                norm = normalize_url(url)
                async with state.lock:
                    if norm in state.visited or state.processed >= cfg.max_pages:
                        continue
                    state.visited.add(norm)
                    state.processed += 1
                    emit_progress(cfg.verbose, f"[{state.processed}/{cfg.max_pages}] {url}")

                if not cfg.ignore_robots and not await pipeline.robots.is_allowed(url):
                    continue
                await state.pace(url, await pipeline.robots.crawl_delay(url))

                record = await pipeline.run_one(norm)
                writer.write(record)

                raw_html = getattr(record, "_raw_html", "") or ""
                if record.error is None and raw_html:
                    for href in pipeline.discover_links(record.url, raw_html):
                        if not any(in_domain(href, d) for d in state.domains):
                            continue
                        if not _keepable(href):
                            continue
                        href_n = normalize_url(href)
                        async with state.lock:
                            if href_n in state.visited:
                                continue
                        if not cfg.ignore_robots and not await pipeline.robots.is_allowed(href):
                            continue
                        await state.schedule(href)
            finally:
                await state.finish()

    workers = [asyncio.create_task(worker()) for _ in range(max(1, cfg.concurrency))]
    await asyncio.gather(*workers)