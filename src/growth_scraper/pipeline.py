"""Orchestrates one URL through Crawl4AI plus our stages."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import random
import re
import uuid
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from . import apipage, extractors, protection
from .structured import extract_structured
from .meta import detect_language, extract_meta
from .screenshot import capture_screenshot
from .waitcontent import needs_wait, wait_for_content
from .clickguard import GUARD_JS
from .parse import parse_fields
from .highlights import highlights_for_query
from .documents import detect_doc_format, extract_document, MAX_DOC_BYTES
from .maincontent import extract_main_text
from .pagemeta import extract_links, extract_images_info
from .utils import build_headers, browser_headers
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


async def _download_images(page_url: str, image_urls: list[str], export_dir: str, cfg=None) -> list[str]:
    global _HTTPX_IMAGE
    if not image_urls:
        return []
    import httpx  # lazy

    headers = build_headers(cfg)
    _HTTPX_IMAGE = _HTTPX_IMAGE or httpx.Client(timeout=15, follow_redirects=True, headers=headers)
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


_LINK_MD = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_DATA_IMG_MD = re.compile(r"!\[([^\]]*)\]\(data:[^)]+\)")


def _strip_markdown_links(text: str) -> str:
    """Keep anchor text, drop URLs (markdownParams.includeLinks=false)."""
    if not text or "](" not in text:
        return text
    return _LINK_MD.sub(r"\1", text)


def _strip_data_images(text: str) -> str:
    """Drop inline data-URI images (Asana serves SVG icons as data: URIs that
    would otherwise pollute `text` with kilobytes of base64). Keeps alt text
    when present, drops the tag otherwise. Always on, fail-open."""
    if not text or "data:" not in text:
        return text
    try:
        return _DATA_IMG_MD.sub(lambda m: m.group(1).strip(), text)
    except Exception:
        return text


# Body markers of a degraded/soft-blocked render (Airtable serves its
# #legacyEnterprise fallback with HTTP 200 to headless Chromium, verified
# live sep-2026). Checked in the FIRST 3000 chars only — banners live at the
# top, while articles merely mentioning browsers live further down.
_DEGRADED_BODY_MARKERS = [
    "browser version is not supported",
    "browser is no longer supported",
    "please upgrade your browser",
    "javascript is disabled",
    "enable javascript to",
    "javascript is required",
]


def _is_degraded_text(text: str) -> bool:
    if not text:
        return False
    try:
        head = text[:3000].lower()
        return any(m in head for m in _DEGRADED_BODY_MARKERS)
    except Exception:
        return False


def _apply_exclude_selectors(html: str, selectors: list[str] | None) -> str:
    """Remove matching nodes from HTML (returns cleaned HTML, fail-open)."""
    if not html or not selectors:
        return html
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for sel in selectors:
            sel = (sel or "").strip()
            if not sel:
                continue
            try:
                for el in soup.select(sel):
                    el.decompose()
            except Exception:
                continue
        return str(soup)
    except Exception:
        return html


def _text_from_cleaned_html(html: str, max_chars: int = 0) -> str:
    """Plain-text fallback from (possibly exclusion-filtered) HTML."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())
    except Exception:
        return ""
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        text = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return text


def _parse_wait_ms(spec: str | None) -> int | None:
    """'1200' / '1200ms' / '2s' -> milliseconds. None when not a duration."""
    if not spec:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(ms|s)?\s*", spec)
    if not m:
        return None
    n, unit = int(m.group(1)), (m.group(2) or "ms")
    return n * 1000 if unit == "s" else n


def _cap_text(text: str, max_chars: int = 0) -> str:
    """Word-boundary cap shared by HTML and document text."""
    text = " ".join((text or "").split())
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        text = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return text


def _parse_max_age(spec: str | None) -> float:
    """'24h' / '7d' / '30m' / '3600' (seconds) -> seconds. 0 = forever."""
    if not spec:
        return 0.0
    m = re.fullmatch(r"\s*(\d+)\s*(ms|s|m|h|d)?\s*", str(spec))
    if not m:
        return 0.0
    n, unit = int(m.group(1)), (m.group(2) or "s")
    return {"ms": n / 1000, "s": n, "m": n * 60, "h": n * 3600, "d": n * 86400}[unit]


def _cache_fresh(record, max_age_s: float) -> bool:
    """True when the cached record is still within TTL (or TTL is off)."""
    if not max_age_s or not getattr(record, "scrapedAt", ""):
        return True
    try:
        from datetime import datetime, timezone

        scraped = datetime.fromisoformat(record.scrapedAt)
        if scraped.tzinfo is None:
            scraped = scraped.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - scraped).total_seconds()
        return age <= max_age_s
    except Exception:
        return True


async def _fetch_document(url: str, cfg) -> tuple[bytes, str, str] | None:
    """Single polite GET for a document URL. (data, content_type, final_url)."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                     headers=build_headers(cfg)) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                ctype = resp.headers.get("content-type", "")
                chunks, total = [], 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_DOC_BYTES:
                        break
                return b"".join(chunks), ctype, str(resp.url)
    except Exception:
        return None


def _match_downloaded(img_urls: list[str], saved: list[str]) -> dict[str, str]:
    """Map image URL -> saved path. Downloads can fail, so match by digest."""
    by_name = {}
    for p in saved:
        by_name[os.path.basename(p)] = p
    out = {}
    for u in img_urls:
        digest = hashlib.sha1(u.encode()).hexdigest()[:12]
        ext = os.path.splitext(urlparse(u).path)[1] or ".jpg"
        hit = by_name.get(f"{digest}{ext}")
        if hit:
            out[u] = hit
    return out


def _enrich_image_dimensions(images_info: list[dict] | None, url_to_path: dict[str, str]) -> None:
    """Fill width/height from downloaded files (Pillow). In-place, fail-open."""
    if not images_info:
        return
    try:
        from PIL import Image

        for info in images_info:
            if info.get("width") and info.get("height"):
                continue
            path = url_to_path.get(info.get("url", ""))
            if not path:
                continue
            try:
                with Image.open(path) as im:
                    info["width"], info["height"] = im.size
            except Exception:
                continue
    except Exception:
        pass


def _build_summary(url: str, title: str, page_type: str, items: list, text: str, html: str,
                   structured: dict | None = None, parsed: dict | None = None,
                   highlights: list | None = None, query: str | None = None,
                   links: list | None = None, images_info: list | None = None,
                   document: dict | None = None, degraded: bool = False) -> dict:
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
    if parsed:
        result["parsedKeys"] = sorted(parsed.keys())
    if highlights:
        result["highlightsCount"] = len(highlights)
    if query:
        result["query"] = query
    if links:
        result["linksCount"] = len(links)
    if images_info:
        result["imagesCount"] = len(images_info)
    if document:
        result["docFormat"] = document.get("format")
    if degraded:
        result["degraded"] = True
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
            headers=browser_headers(cfg),
        )
        run_kwargs = dict(
            cache_mode=CacheMode.BYPASS,
            # False, not True: this is crawl4ai's own request/response capture,
            # separate from and redundant with netrec.py (D4 — built netrec.py
            # specifically because this one "no da bodies de forma fiable").
            # Nothing reads its output. Leaving it on did real, wasted work on
            # every request/response AND hit a crawl4ai bug on non-text bodies
            # (UnboundLocalError on `text_body`, logged as a [CAPTURE] warning
            # on every page with an empty/binary response — e.g. tracking
            # beacons like bodas.net's /trace/internalTracking.php).
            capture_network_requests=False,
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
            # Generic SPA wait (opt-in --wait-for): duration or CSS selector.
            if cfg.wait_for:
                try:
                    ms = _parse_wait_ms(cfg.wait_for)
                    if ms is not None:
                        await asyncio.sleep(min(ms, 10_000) / 1000)
                    else:
                        await page.wait_for_selector(cfg.wait_for, timeout=min(cfg.page_timeout_ms, 10_000))
                except Exception as exc:
                    emit_progress(cfg.verbose, f"wait-for on {url} skipped: {exc}")
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
        # Late consent re-check (1 quick pass, no second-chance wait): slow CMPs
        # (HubSpot's banner SDK renders seconds after load, verified live) are
        # missed by the after_goto pass. By now expand+scroll bought us seconds,
        # so a late banner is present and clickable. No-op when already clean.
        if cfg.handle_consent:
            try:
                late = await handle_consent(page, iterations=1, late_wait=False)
                emit_progress(cfg.verbose, f"late consent on {url or session.page_url}: {late}")
            except Exception as exc:
                emit_progress(cfg.verbose, f"late consent failed: {exc}")
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

    async def _run_document(self, url: str, cfg: ScrapeConfig, crawled_from: str | None) -> Record | None:
        """Fetch + extract a PDF/Office URL into a document record. None = fall through."""
        fetched = await _fetch_document(url, cfg)
        if not fetched:
            return None
        data, ctype, final_url = fetched
        fmt = detect_doc_format(final_url, ctype) or detect_doc_format(url)
        if not fmt:
            return None
        extracted = extract_document(data, fmt)
        if not extracted:
            return None
        text, info = extracted
        record = Record(url=url, crawledFrom=crawled_from)
        record.statusCode = 200
        record.finalUrl = final_url
        path = urlparse(final_url).path.rstrip("/")
        record.title = path.rsplit("/", 1)[-1] or (urlparse(final_url).hostname or url)
        record.text = _cap_text(text, cfg.max_text_chars)
        record.pageType = "document"
        record.document = info
        record.highlights = highlights_for_query(record.text, cfg.query or "", max_n=cfg.max_highlights) or None
        record.summary = _build_summary(url, record.title, record.pageType, [], record.text, "",
                                        query=cfg.query, document=info)
        lang = detect_language("", record.text)
        if lang:
            record.summary["language"] = lang
        if cfg.cache_dir:
            self._cache_write(url, record, cfg.cache_dir)
        return record

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

        # local record cache (with --max-age TTL)
        if cfg.cache_dir and not crawled_from:
            cached = self._cache_read(url, cfg.cache_dir)
            if cached is not None and _cache_fresh(cached, cfg.max_age_s):
                cached.crawledFrom = crawled_from
                cached.fromCache = True
                emit_progress(cfg.verbose, f"cache hit: {url}")
                return cached

        # Documents (PDF/DOCX/XLSX/PPTX): single polite GET, no browser.
        if detect_doc_format(url):
            record = await self._run_document(url, cfg, crawled_from)
            if record is not None:
                return record
            # fall through to the browser pipeline on any failure

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
        record.text = _strip_data_images(record.text)

        # Context.dev-inspired content controls (all opt-in, fail-open).
        filtered_html = raw_html
        if cfg.exclude_selectors:
            filtered_html = _apply_exclude_selectors(raw_html, cfg.exclude_selectors)
            # Exclusions only matter if the text reflects them: recompute
            # plain text from the filtered HTML (markdown came pre-exclusion).
            recomputed = _text_from_cleaned_html(filtered_html, max_chars=cfg.max_text_chars)
            if recomputed:
                record.text = recomputed
        if not cfg.include_links:
            record.text = _strip_markdown_links(record.text)
        if cfg.main_content:
            main_text = extract_main_text(filtered_html, max_chars=cfg.max_text_chars)
            if main_text:
                record.text = main_text
        record.parsed = parse_fields(filtered_html, cfg.parse_rules) or None
        record.highlights = highlights_for_query(record.text, cfg.query or "", max_n=cfg.max_highlights) or None
        record.links = extract_links(filtered_html or raw_html, record.finalUrl or url) or None
        record.imagesInfo = extract_images_info(filtered_html or raw_html, record.finalUrl or url) or None
        record.degraded = _is_degraded_text(record.text)

        # P1: page-type extractors
        if result.cleaned_html or result.html:
            record.pageType, record.items = extractors.run_extraction(result)

        record.structured = extract_structured(raw_html)
        record.pageType, record.items = extractors.reconcile_page_type(
            record.pageType, record.items, record.structured
        )
        record.summary = _build_summary(url, record.title, record.pageType, record.items, record.text, raw_html, record.structured,
                                        parsed=record.parsed, highlights=record.highlights, query=cfg.query,
                                        links=record.links, images_info=record.imagesInfo,
                                        degraded=record.degraded)

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
                record.images = await _download_images(url, img_urls, _images_dir(cfg.export_images), cfg)
                if record.images and record.imagesInfo:
                    url_to_path = _match_downloaded(img_urls, record.images)
                    _enrich_image_dimensions(record.imagesInfo, url_to_path)

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