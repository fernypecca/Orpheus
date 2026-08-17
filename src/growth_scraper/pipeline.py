"""Orchestrates one URL through Crawl4AI plus our stages."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from . import apipage, extractors, protection
from .clickguard import GUARD_JS
from .config import ROBOTS_UA_TOKEN, ScrapeConfig, Record
from .consent import handle_consent
from .expand import expand_and_scroll
from .netrec import NetworkRecorder
from .records import emit_progress
from .robots import RobotsPolicy

_HONEST_UA = ROBOTS_UA_TOKEN
_HTTPX_IMAGE = None  # lazy import httpx


class Session:
    """Mutable state shared between crawl4ai hooks and our pipeline."""

    def __init__(self):
        self.netrec = NetworkRecorder()
        self.page = None
        self.page_url = ""
        self.replayed: list[dict] = []


def _hook_attach(session: Session):
    async def on_page_context_created(page, context, **kwargs):
        session.page = page
        await session.netrec.attach(page)
        await page.evaluate(GUARD_JS)
        return page

    return on_page_context_created


def _hook_after_goto(session: Session, cfg: ScrapeConfig):
    async def after_goto(page, context, url, response, **kwargs):
        session.page = page
        try:
            if cfg.handle_consent:
                status = await handle_consent(page)
                emit_progress(cfg.verbose, f"consent on {url}: {status}")
            if cfg.expand:
                summary = await expand_and_scroll(page, cfg, session.netrec)
                emit_progress(cfg.verbose, f"probe on {url}: {summary}")
        except Exception as exc:
            emit_progress(cfg.verbose, f"after_goto probe failed for {url}: {exc}")
        return page

    return after_goto


def _hook_before_retrieve_html(session: Session, cfg: ScrapeConfig):
    async def before_retrieve_html(page, context, **kwargs):
        # P1: the page is alive here and has already fired its XHRs; replay any
        # paginated internal APIs so we capture more than the default load.
        if cfg.capture_apis and cfg.expand and session.page_url:
            try:
                session.netrec.deactivate()  # replay fetches must not be re-captured
                captured = session.netrec.snapshot()
                session.replayed = await apipage.replay_pagination(
                    page, session.page_url, captured
                )
                if session.replayed:
                    emit_progress(cfg.verbose, f"pagination replay: +{len(session.replayed)} responses")
            except Exception as exc:
                emit_progress(cfg.verbose, f"pagination replay failed: {exc}")
        return page

    return before_retrieve_html


def _images_dir(base: str) -> str:
    os.makedirs(base, exist_ok=True)
    return base


async def _download_images(page_url: str, media, export_dir: str) -> list[str]:
    global _HTTPX_IMAGE
    if not media or not media.get("images"):
        return []
    import httpx  # lazy

    _HTTPX_IMAGE = _HTTPX_IMAGE or httpx.Client(timeout=15, follow_redirects=True)
    saved: list[str] = []
    for img in media["images"][:20]:
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        url = urljoin(page_url, src)
        digest = hashlib.sha1(url.encode()).hexdigest()[:12]
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        path = os.path.join(export_dir, f"{digest}{ext}")
        try:
            resp = _HTTPX_IMAGE.get(url)
            if resp.status_code == 200 and resp.content:
                with open(path, "wb") as f:
                    f.write(resp.content)
                saved.append(path)
        except Exception:
            continue
    return saved


def _text_from_result(result) -> str:
    if result.markdown and getattr(result.markdown, "raw_markdown", None):
        return result.markdown.raw_markdown or ""
    html = result.cleaned_html or result.html or ""
    if not html:
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def _title_from_result(result) -> str:
    if result.metadata and result.metadata.get("title"):
        return result.metadata["title"]
    html = result.cleaned_html or result.html or ""
    if html:
        from bs4 import BeautifulSoup

        h1 = BeautifulSoup(html, "html.parser").select_one("h1")
        if h1:
            return " ".join(h1.get_text(" ", strip=True).split())
    return ""


class Pipeline:
    def __init__(self, cfg: ScrapeConfig, robots: RobotsPolicy):
        self.cfg = cfg
        self.robots = robots
        self.session = Session()
        self._last_result = None
        self.browser_cfg = BrowserConfig(
            headless=not cfg.headful,
            text_mode=True,
            verbose=cfg.verbose,
            user_agent=_HONEST_UA,
        )
        self.run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            capture_network_requests=True,
            remove_consent_popups=False,  # we handle consent ourselves (reject-only)
            wait_until="load",
            page_timeout=cfg.page_timeout_ms,
            excluded_tags=["nav", "footer", "form", "aside", "script", "style"],
            remove_overlay_elements=False,  # crawl4ai's overlay remover can nuke whole pages (e.g. Wikipedia); our consent handler covers real overlays
            word_count_threshold=3,
            verbose=cfg.verbose,
        )

    async def start(self):
        self.crawler = AsyncWebCrawler(config=self.browser_cfg)
        self.crawler.crawler_strategy.set_hook(
            "on_page_context_created", _hook_attach(self.session)
        )
        self.crawler.crawler_strategy.set_hook(
            "after_goto", _hook_after_goto(self.session, self.cfg)
        )
        self.crawler.crawler_strategy.set_hook(
            "before_retrieve_html", _hook_before_retrieve_html(self.session, self.cfg)
        )
        await self.crawler.start()

    async def close(self):
        try:
            await self.crawler.close()
        except Exception:
            pass

    async def run_one(self, url: str, crawled_from: str | None = None) -> Record:
        record = Record(url=url, crawledFrom=crawled_from)

        # robots.txt (default: respected)
        if not self.cfg.ignore_robots:
            allowed = await self.robots.is_allowed(url)
            if not allowed:
                record.error = "ROBOTS_BLOCKED: disallowed by robots.txt"
                emit_progress(self.cfg.verbose, f"robots.txt blocks {url}")
                return record

        # local record cache
        if self.cfg.cache_dir and not crawled_from:
            cached = self._cache_read(url)
            if cached is not None:
                cached.crawledFrom = crawled_from
                emit_progress(self.cfg.verbose, f"cache hit: {url}")
                return cached

        emit_progress(self.cfg.verbose, f"crawling {url}")
        self.session.netrec.reset(url)
        self.session.page_url = url
        self.session.replayed = []

        try:
            result = await self.crawler.arun(url, config=self.run_cfg)
        except Exception as exc:
            record.error = f"CRAWL_ERROR: {exc}"
            return record

        record.statusCode = getattr(result, "status_code", None)
        self._last_result = result

        # fail-closed anti-bot detection
        reason = protection.detect(result)
        if reason:
            record.error = reason
            record.protectionBlocked = True
            emit_progress(self.cfg.verbose, reason)
            return record

        record.title = _title_from_result(result)
        record.text = _text_from_result(result)

        # P1: page-type extractors
        if result.cleaned_html or result.html:
            record.pageType, record.items = extractors.run_extraction(result)

        # P0/P1: API responses + pagination replay (deduped by URL)
        if self.cfg.capture_apis:
            seen_urls: set[str] = set()
            api_responses: list[dict] = []
            for entry in self.session.netrec.snapshot() + self.session.replayed:
                u = entry.get("url")
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                api_responses.append(entry)
            record.apiResponses = api_responses[: self.cfg.max_api_responses]

        # optional image export (P2 multimodal pointer)
        if self.cfg.export_images and result.media:
            record.images = await _download_images(url, result.media, _images_dir(self.cfg.export_images))

        if self.cfg.cache_dir:
            self._cache_write(url, record)
        return record

    # -- link discovery for crawl mode ----------------------------------------
    def discover_links(self, url: str) -> list[str]:
        """Extract same-host links from the raw HTML.

        crawl4ai's `result.links` is derived from the *cleaned* DOM, so links
        living inside <nav>/<footer> (which we exclude) would be missed. We
        parse the original HTML instead.
        """
        html = getattr(self._last_result, "html", "") or ""
        if not html:
            return []
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        hrefs: list[str] = []
        for a in soup.find_all("a", href=True):
            hrefs.append(urljoin(url, a["href"]))
        return list(dict.fromkeys(hrefs))

    # -- local record cache (scenario 5: second run must not reprocess) -------
    def _cache_path(self, url: str) -> str:
        digest = hashlib.sha1(url.encode()).hexdigest()[:24]
        return os.path.join(self.cfg.cache_dir, f"record-{digest}.json")

    def _cache_read(self, url: str) -> Record | None:
        try:
            path = self._cache_path(url)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            record = Record(**{k: data[k] for k in Record.__dataclass_fields__ if k in data})
            return record
        except Exception:
            return None

    def _cache_write(self, url: str, record: Record) -> None:
        try:
            os.makedirs(self.cfg.cache_dir, exist_ok=True)
            with open(self._cache_path(url), "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, ensure_ascii=False)
        except Exception:
            pass