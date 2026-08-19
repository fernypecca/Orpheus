"""Command-line interface for gscrape."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from urllib.parse import urlparse

from .config import (
    DEFAULT_DELAY,
    DEFAULT_JITTER,
    DEFAULT_MAX_API_RESPONSES,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_TEXT_CHARS,
    SITEMAP_MAX_URLS,
    ScrapeConfig,
)
from .crawl import crawl_seeds
from .pacing import Pacer
from .pipeline import Pipeline
from .records import CsvWriter, JsonlWriter, emit_progress
from .robots import RobotsPolicy
from .urlutil import normalize_url


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gscrape",
        description="Polite, LLM-ready web scraper for growth marketing research (built on Crawl4AI).",
    )
    p.add_argument("urls", nargs="*", help="URLs to scrape (or use --urls-file).")
    p.add_argument("-f", "--urls-file", help="File with one URL per line.")
    p.add_argument("--crawl", action="store_true", help="Follow same-domain links from the seed URLs.")
    p.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Page cap for --crawl.")
    p.add_argument("--concurrency", type=int, default=2, help="Parallel page loads during --crawl (robots/delay respected).")
    p.add_argument("--sitemap", nargs="?", const="auto", metavar="URL",
                   help="Seed from a sitemap: '--sitemap' uses <seed>/sitemap.xml per seed; '--sitemap URL' uses an explicit sitemap.")
    p.add_argument("-o", "--output", default="out.jsonl", help="Output .jsonl path.")
    p.add_argument("--csv", action="store_true", help="Also write a flat .csv summary next to the .jsonl.")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Base seconds between requests.")
    p.add_argument("--jitter", type=float, default=DEFAULT_JITTER, help="Random extra delay added on top of --delay.")
    p.add_argument("--ignore-robots", action="store_true", help="Bypass robots.txt (default: respected).")
    p.add_argument("--cache-dir", help="Local cache dir (records + robots.txt). Re-run avoids reprocessing.")
    p.add_argument("--no-expand", action="store_true", help="Skip collapsed-content expansion / scrolling probe.")
    p.add_argument("--no-apis", action="store_true", help="Skip apiResponses capture and pagination replay.")
    p.add_argument("--no-consent", action="store_true", help="Skip consent-banner rejection/hiding.")
    p.add_argument("--max-text-chars", type=int, default=DEFAULT_MAX_TEXT_CHARS,
                   help="Cap `text` to keep records LLM/TPM-friendly (0 = no cap).")
    p.add_argument("--fit-text", action="store_true",
                   help="Use crawl4ai's pruned markdown (fit_markdown) as `text` instead of raw markdown.")
    p.add_argument("--raw-html", action="store_true",
                   help="Include unprocessed `rawHtml` in each record (for consumers that parse inline "
                        "<script type=application/json> data crawl4ai's cleaning would strip).")
    p.add_argument("--max-retries", type=int, default=2, help="Retry transient errors/timeouts/5xx with backoff.")
    p.add_argument("--no-frames", action="store_true", help="Skip iframe capture (frames/frameTexts).")
    p.add_argument("--consent-wait-ms", type=int, default=5000, help="Max ms to wait for gated content after consent dismissal.")
    p.add_argument("--anti-bot-retries", type=int, default=2, help="Retry HTTP 403/429 with long backoff.")
    p.add_argument("--anti-bot-backoff", type=float, default=15.0, help="Base seconds between 403/429 retries.")
    p.add_argument("--export-images", metavar="DIR", help="Save page images into DIR (P2 multimodal pointer).")
    p.add_argument("--screenshots", metavar="DIR", help="Save a full-page PNG per URL into DIR.")
    p.add_argument("--max-api-responses", type=int, default=DEFAULT_MAX_API_RESPONSES)
    p.add_argument("--page-timeout", type=int, default=30_000, help="Page load timeout in ms.")
    p.add_argument("--headful", action="store_true", help="Show the browser window.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _collect_urls(args) -> list[str]:
    urls: list[str] = []
    if args.urls_file:
        try:
            with open(args.urls_file, "r", encoding="utf-8") as f:
                urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        except OSError as exc:
            print(f"[gscrape] error: cannot read urls file: {exc}", file=sys.stderr)
            sys.exit(2)
    urls += [u for u in args.urls if u]
    urls = [normalize_url(u) for u in urls]
    bad = [u for u in urls if urlparse(u).scheme not in ("http", "https")]
    if bad:
        print(f"[gscrape] error: invalid URLs (must be http/https): {bad}", file=sys.stderr)
        sys.exit(2)
    return list(dict.fromkeys(urls))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        from .server import serve_main

        return serve_main(argv[1:])
    args = build_parser().parse_args(argv)
    urls = _collect_urls(args)
    if not urls and args.sitemap is None:
        print("[gscrape] error: provide at least one URL, --urls-file, or --sitemap", file=sys.stderr)
        return 2
    if args.sitemap == "auto" and not urls:
        print("[gscrape] error: --sitemap (auto) needs at least one seed URL", file=sys.stderr)
        return 2

    cfg = ScrapeConfig(
        output=args.output,
        delay=args.delay,
        jitter=args.jitter,
        ignore_robots=args.ignore_robots,
        cache_dir=args.cache_dir,
        max_pages=args.max_pages,
        concurrency=max(1, args.concurrency),
        expand=not args.no_expand,
        capture_apis=not args.no_apis,
        handle_consent=not args.no_consent,
        export_images=args.export_images,
        screenshot_dir=args.screenshots,
        max_api_responses=args.max_api_responses,
        max_text_chars=args.max_text_chars,
        fit_text=args.fit_text,
        raw_html=args.raw_html,
        max_retries=max(0, args.max_retries),
        fetch_frames=not args.no_frames,
        consent_wait_ms=max(0, args.consent_wait_ms),
        anti_bot_retries=max(0, args.anti_bot_retries),
        anti_bot_backoff_s=args.anti_bot_backoff,
        csv_output=args.csv,
        page_timeout_ms=args.page_timeout,
        headful=args.headful,
        verbose=args.verbose,
    )

    robots = RobotsPolicy(cache_dir=args.cache_dir)
    pipeline = Pipeline(cfg, robots)

    async def run_all():
        await pipeline.start()
        try:
            if args.sitemap is not None:
                from .sitemap import fetch_sitemap_urls

                seed_urls = urls or [args.sitemap]
                if args.sitemap == "auto":
                    targets = []
                    for seed in seed_urls:
                        targets += await fetch_sitemap_urls(seed, robots, SITEMAP_MAX_URLS)
                else:
                    targets = await fetch_sitemap_urls(args.sitemap, robots, SITEMAP_MAX_URLS)
                if not targets:
                    print("[gscrape] error: sitemap yielded no usable URLs", file=sys.stderr)
                    return 1
                urls[:] = targets
                emit_progress(cfg.verbose, f"sitemap seed: {len(urls)} URLs")

            with JsonlWriter(cfg.output) as jsonl:
                with CsvWriter(_csv_path(cfg.output)) if cfg.csv_output else _noop() as csv_out:
                    writer = _FanOut([jsonl, csv_out])
                    if args.crawl:
                        await crawl_seeds(pipeline, urls, cfg, writer)
                    else:
                        pacer = Pacer(cfg)
                        for i, url in enumerate(urls, 1):
                            emit_progress(cfg.verbose, f"[{i}/{len(urls)}] {url}")
                            record = await pipeline.run_one(url)
                            writer.write(record)
                            if i < len(urls):
                                await pacer.wait(await pipeline.robots.crawl_delay(url))
                emit_progress(cfg.verbose, f"done. wrote {jsonl.path}")
        except KeyboardInterrupt:
            print("\n[gscrape] interrupted.", file=sys.stderr)
            raise
        finally:
            await pipeline.close()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        return 130
    return 0


def _csv_path(output: str) -> str:
    return os.path.splitext(output)[0] + ".csv"


class _FanOut:
    """Writes each record to every writer (JSONL + optional CSV mirror)."""

    def __init__(self, writers: list):
        self._writers = [w for w in writers if w is not None]

    def write(self, record) -> None:
        for w in self._writers:
            w.write(record)


def _noop():
    """Context manager that does nothing (used when --csv is off)."""
    class _Noop:
        def __enter__(self):
            return None
        def __exit__(self, *exc):
            return False
    return _Noop()


if __name__ == "__main__":
    raise SystemExit(main())