"""Orchestrates one URL through Crawl4AI plus our stages."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import random
import uuid
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from . import apipage, extractors, protection
from .structured import extract_structured
from .meta import detect_language, extract_meta
from .screenshot import capture_screenshot
from .waitcontent import needs_wait, wait_for_content
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
    """Mutable state shared between crawl4ai hooks and our pipeline.

    One instance per page run (concurrent crawls keep their own state).
    """

    def __init__(self):
        self.netrec = NetworkRecorder()
        self.page = None
        self.page_url = ""
        self.replayed: list[dict] = []
        self.cfg: "ScrapeConfig | None" = None
        self.screenshot_path: str | None = None


def _images_dir(base: str) -> str:
    os.makedirs(base, exist_ok=True)
    return base


async def _download_images(page_url: str, image_urls: list[str], export_dir: str) -> list[str]:
    global _HTTPX_IMAGE
    if not image_urls:
        return []
    import httpx  # lazy

    _HTTPX_IMAGE = _HTTPX_IMAGE or httpx.Client(timeout=15, follow_redirects=True)
    saved: list[str] = []
    for url in image_urls:
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


def _extract_image_urls(page_url: str, html: str, limit: int = 20) -> list[str]:
    """Image URLs from the raw HTML.

    We parse the original HTML ourselves instead of relying on crawl4ai's
    `result.media`: with `excluded_tags` set (which we need for clean text),
    crawl4ai 0.9.2 silently captures zero images. This also handles lazy-loaded
    `srcset`/`data-src` attributes.
    """
    if not html:
        return []
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src or src.startswith("data:"):
            srcset = img.get("srcset")
            if srcset:
                candidates = [c.strip().rsplit(" ", 1) for c in srcset.split(",") if c.strip()]
                candidates = [c[0] for c in candidates if c and c[0]]
                src = candidates[-1] if candidates else ""
            else:
                continue
        if not src or src.startswith("data:"):
            continue
        resolved = urljoin(page_url, src)
        if resolved in seen:
            continue
        seen.add(resolved)
        urls.append(resolved)
        if len(urls) >= limit:
            break
    return urls


def _text_from_result(result, fit_text: bool = False, max_chars: int = 0) -> str:
    md = getattr(result, "markdown", None)
    text = ""
    if md is not None:
        if fit_text and getattr(md, "fit_markdown", ""):
            text = md.fit_markdown
        elif getattr(md, "raw_markdown", ""):
            text = md.raw_markdown
    if not text:
        html = result.cleaned_html or result.html or ""
        if html:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            text = " ".join(soup.get_text(" ", strip=True).split())
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        text = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return text


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


def _build_summary(url: str, title: str, page_type: str, items: list, text: str, html: str,
                   structured: dict | None = None) -> dict:
    """Cheap structured triage fields so growth marketers can filter before the LLM."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    meta, h1 = "", ""
    if html:
        from bs4 import BeautifulSoup

        try:
            soup = BeautifulSoup(html, "html.parser")
            m = soup.select_one("meta[name='description']")
            if m:
                meta = " ".join((m.get("content") or "").split())
            h = soup.select_one("h1")
            if h:
                h1 = " ".join(h.get_text(" ", strip=True).split())
        except Exception:
            pass
    result = {
        "domain": host,
        "title": title,
        "metaDescription": meta,
        "h1": h1,
        "wordCount": len(text.split()),
        "itemCount": len(items),
        "pageType": page_type,
    }
    if structured:
        price = structured.get("price") or {}
        rating = structured.get("rating") or {}
        result["structuredSource"] = structured.get("source")
        result["structuredPrice"] = price.get("value")
        result["structuredRatingValue"] = rating.get("value")
        result["structuredReviewCount"] = rating.get("count")
        result["structuredCategory"] = structured.get("category")
    return result


class Pipeline:
    def __init__(self, cfg: ScrapeConfig, robots: RobotsPolicy):
        self.cfg = cfg
        self.robots = robots
        self.session = Session()  # fallback when a hook gets no session_id
        self.session.cfg = self.cfg
        self._sessions: dict[str, Session] = {}
        self._last_result = None
        self.browser_cfg = BrowserConfig(
            headless=not cfg.headful,
            text_mode=True,
            verbose=cfg.verbose,
            user_agent=_HONEST_UA,
        )
        run_kwargs = dict(
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
        if cfg.fit_text:
            from crawl4ai.content_filter_strategy import PruningContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

            run_kwargs["markdown_generator"] = DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(min_word_threshold=4)
            )
        self.run_cfg = CrawlerRunConfig(**run_kwargs)

    # -- hooks (per-run sessions keyed by the config.session_id each arun carries)
    def _session_for(self, config) -> Session:
        sid = getattr(config, "session_id", None)
        return self._sessions.get(sid) or self.session

    async def _hook_attach(self, page, context, config=None, **kwargs):
        session = self._session_for(config)
        session.page = page
        await session.netrec.attach(page)
        await page.evaluate(GUARD_JS)
        return page

    async def _hook_after_goto(self, page, context, url, config=None, **kwargs):
        session = self._session_for(config)
        cfg = session.cfg or self.cfg
        session.page = page
        try:
            if cfg.handle_consent:
                status = await handle_consent(page)
                emit_progress(cfg.verbose, f"consent on {url}: {status}")
                if needs_wait(status):
                    waited = await wait_for_content(page, cfg)
                    emit_progress(cfg.verbose, f"wait_for_content on {url}: {waited}")
            if cfg.expand:
                summary = await expand_and_scroll(page, cfg, session.netrec)
                emit_progress(cfg.verbose, f"probe on {url}: {summary}")
        except Exception as exc:
            emit_progress(cfg.verbose, f"after_goto probe failed for {url}: {exc}")
        return page

    async def _hook_before_retrieve_html(self, page, context, url="", config=None, **kwargs):
        # P1: the page is alive here and has already fired its XHRs; replay any
        # paginated internal APIs so we capture more than the default load.
        session = self._session_for(config)
        cfg = session.cfg or self.cfg
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
        # Fase 3: full-page screenshot (CLI-only) while the page is alive+expanded
        if cfg.screenshot_dir and session.page:
            session.screenshot_path = await capture_screenshot(
                session.page, url or session.page_url, cfg.screenshot_dir
            )
            if session.screenshot_path:
                emit_progress(cfg.verbose, f"screenshot: {session.screenshot_path}")
        return page

    async def start(self):
        self.crawler = AsyncWebCrawler(config=self.browser_cfg)
        self.crawler.crawler_strategy.set_hook(
            "on_page_context_created", self._hook_attach
        )
        self.crawler.crawler_strategy.set_hook(
            "after_goto", self._hook_after_goto
        )
        self.crawler.crawler_strategy.set_hook(
            "before_retrieve_html", self._hook_before_retrieve_html
        )
        await self.crawler.start()

    async def close(self):
        try:
            await self.crawler.close()
        except Exception:
            pass

    async def run_one(self, url: str, crawled_from: str | None = None,
                      cfg: ScrapeConfig | None = None) -> Record:
        cfg = cfg or self.cfg
        record = Record(url=url, crawledFrom=crawled_from)

        # robots.txt (default: respected)
        if not cfg.ignore_robots:
            allowed = await self.robots.is_allowed(url)
            if not allowed:
                record.error = "ROBOTS_BLOCKED: disallowed by robots.txt"
                emit_progress(cfg.verbose, f"robots.txt blocks {url}")
                return record

        # local record cache
        if cfg.cache_dir and not crawled_from:
            cached = self._cache_read(url, cfg.cache_dir)
            if cached is not None:
                cached.crawledFrom = crawled_from
                cached.fromCache = True
                emit_progress(cfg.verbose, f"cache hit: {url}")
                return cached

        emit_progress(cfg.verbose, f"crawling {url}")

        # retry loop: 5xx + 403/429 with long backoff
        attempts = 1 + cfg.max_retries + cfg.anti_bot_retries
        result = None
        last_exc: Exception | None = None
        used_session: Session | None = None
        retries_done = 0
        for attempt in range(attempts):
            sid = uuid.uuid4().hex
            session = Session()
            session.page_url = url
            session.cfg = cfg
            session.netrec.reset(url)  # starts the recorder active for this page
            self._sessions[sid] = session
            run_cfg = copy.copy(self.run_cfg)
            run_cfg.session_id = sid
            try:
                result = await self.crawler.arun(url, config=run_cfg)
                if result is None:
                    raise RuntimeError("crawl4ai returned no result")
                status = getattr(result, "redirected_status_code", None)
                if status is None:
                    status = getattr(result, "status_code", None)
                if status in (403, 429) and attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: status {status} (attempt {attempt + 1}/{attempts})")
                    result = None
                    retries_done += 1
                    await asyncio.sleep(cfg.anti_bot_backoff_s + random.uniform(0, cfg.jitter))
                    continue
                if status is not None and status >= 500 and attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: status {status} (attempt {attempt + 1}/{attempts})")
                    result = None
                    retries_done += 1
                    await asyncio.sleep(cfg.retry_backoff * (2 ** attempt))
                    continue
                used_session = session
                break
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: {exc}")
                    await asyncio.sleep(cfg.retry_backoff * (2 ** attempt))
                    retries_done += 1
                    continue
                break
            finally:
                self._sessions.pop(sid, None)
                try:
                    await self.crawler.crawler_strategy.kill_session(sid)
                except Exception:
                    pass

        record.retries = retries_done

        if result is None:
            record.error = f"CRAWL_ERROR: {last_exc or 'no result after retries'}"
            return record

        status = getattr(result, "redirected_status_code", None)
        if status is None:
            status = getattr(result, "status_code", None)
        record.statusCode = status
        final_url = getattr(result, "redirected_url", None)
        record.finalUrl = final_url or url
        if record.statusCode is not None and record.statusCode >= 500:
            record.error = f"HTTP_ERROR: {record.statusCode} after {attempts} attempt(s)"
            emit_progress(cfg.verbose, record.error)
            return record

        self._last_result = result
        raw_html = getattr(result, "html", "") or ""
        setattr(record, "_raw_html", raw_html)
        if cfg.raw_html:
            record.rawHtml = raw_html

        # fail-closed anti-bot detection
        reason = protection.detect(result)
        if reason:
            record.error = reason
            record.protectionBlocked = True
            emit_progress(cfg.verbose, reason)
            return record

        record.title = _title_from_result(result)
        record.text = _text_from_result(result, fit_text=cfg.fit_text, max_chars=cfg.max_text_chars)

        # P1: page-type extractors
        if result.cleaned_html or result.html:
            record.pageType, record.items = extractors.run_extraction(result)

        record.structured = extract_structured(raw_html)
        record.summary = _build_summary(url, record.title, record.pageType, record.items, record.text, raw_html, record.structured)

        # Fase 3: language triage + rich metadata
        lang = detect_language(raw_html, record.text)
        if lang:
            record.summary["language"] = lang
        record.meta = extract_meta(raw_html, url)

        # P0/P1: API responses + pagination replay (deduped by URL)
        if cfg.capture_apis and used_session is not None:
            seen_urls: set[str] = set()
            api_responses: list[dict] = []
            for entry in used_session.netrec.snapshot() + used_session.replayed:
                u = entry.get("url")
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                api_responses.append(entry)
            record.apiResponses = api_responses[: cfg.max_api_responses]

        # optional image export (P2 multimodal pointer)
        if cfg.export_images and raw_html:
            img_urls = _extract_image_urls(url, raw_html)
            if img_urls:
                record.images = await _download_images(url, img_urls, _images_dir(cfg.export_images))

        if used_session is not None and used_session.screenshot_path:
            record.screenshots = [used_session.screenshot_path]

        if cfg.fetch_frames and not record.frames:
            from .iframes import extract_iframes, fetch_frame_texts

            html = getattr(result, "html", "") or ""
            frames = extract_iframes(url, html, cfg.max_frames)
            if frames:
                record.frames = frames
                robots = self.robots
                record.frameTexts = await fetch_frame_texts(
                    [f["src"] for f in frames], cfg, robots
                )
        if cfg.cache_dir:
            self._cache_write(url, record, cfg.cache_dir)
        return record

    # -- link discovery for crawl mode ----------------------------------------
    def discover_links(self, url: str, html: str = "") -> list[str]:
        """Extract same-host links from the raw HTML.

        crawl4ai's `result.links` is derived from the *cleaned* DOM, so links
        living inside <nav>/<footer> (which we exclude) would be missed. We
        parse the original HTML instead.
        """
        if not html:
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
    def _cache_path(self, url: str, cache_dir: str | None = None) -> str:
        digest = hashlib.sha1(url.encode()).hexdigest()[:24]
        return os.path.join(cache_dir or self.cfg.cache_dir, f"record-{digest}.json")

    def _cache_read(self, url: str, cache_dir: str | None = None) -> Record | None:
        try:
            path = self._cache_path(url, cache_dir)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            record = Record(**{k: data[k] for k in Record.__dataclass_fields__ if k in data})
            return record
        except Exception:
            return None

    def _cache_write(self, url: str, record: Record, cache_dir: str | None = None) -> None:
        try:
            os.makedirs(cache_dir or self.cfg.cache_dir, exist_ok=True)
            with open(self._cache_path(url, cache_dir), "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, ensure_ascii=False)
        except Exception:
            pass