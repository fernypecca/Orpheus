"""Crawl mode: follow same-domain links with a page cap."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from .config import ScrapeConfig
from .pacing import Pacer
from .pipeline import Pipeline
from .records import JsonlWriter, emit_progress

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


async def crawl_seeds(pipeline: Pipeline, seeds: list[str], cfg: ScrapeConfig, writer: JsonlWriter) -> None:
    pacer = Pacer(cfg)
    visited: set[str] = set()
    queue: list[str] = list(seeds)
    processed = 0
    domains = {base_host(urlparse(s).hostname or "") for s in seeds}

    while queue and processed < cfg.max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        await pacer.wait(await pipeline.robots.crawl_delay(url))

        record = await pipeline.run_one(url)
        writer.write(record)
        processed += 1
        emit_progress(cfg.verbose, f"[{processed}/{cfg.max_pages}] wrote {url}")

        if record.error is None and getattr(record, "text", ""):
            for href in pipeline.discover_links(url):
                if href in visited or href in queue:
                    continue
                if not any(in_domain(href, d) for d in domains):
                    continue
                if not _keepable(href):
                    continue
                if not cfg.ignore_robots and not await pipeline.robots.is_allowed(href):
                    continue
                queue.append(href)