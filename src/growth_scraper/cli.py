"""Command-line interface for gscrape."""

from __future__ import annotations

import argparse
import asyncio
import sys
from urllib.parse import urlparse

from .config import (
    DEFAULT_DELAY,
    DEFAULT_JITTER,
    DEFAULT_MAX_API_RESPONSES,
    DEFAULT_MAX_PAGES,
    ScrapeConfig,
)
from .crawl import crawl_seeds
from .pacing import Pacer
from .pipeline import Pipeline
from .records import JsonlWriter, emit_progress
from .robots import RobotsPolicy


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gscrape",
        description="Polite, LLM-ready web scraper for growth marketing research (built on Crawl4AI).",
    )
    p.add_argument("urls", nargs="*", help="URLs to scrape (or use --urls-file).")
    p.add_argument("-f", "--urls-file", help="File with one URL per line.")
    p.add_argument("--crawl", action="store_true", help="Follow same-domain links from the seed URLs.")
    p.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Page cap for --crawl.")
    p.add_argument("-o", "--output", default="out.jsonl", help="Output .jsonl path.")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Base seconds between requests.")
    p.add_argument("--jitter", type=float, default=DEFAULT_JITTER, help="Random extra delay added on top of --delay.")
    p.add_argument("--ignore-robots", action="store_true", help="Bypass robots.txt (default: respected).")
    p.add_argument("--cache-dir", help="Local cache dir (records + robots.txt). Re-run avoids reprocessing.")
    p.add_argument("--no-expand", action="store_true", help="Skip collapsed-content expansion / scrolling probe.")
    p.add_argument("--no-apis", action="store_true", help="Skip apiResponses capture and pagination replay.")
    p.add_argument("--no-consent", action="store_true", help="Skip consent-banner rejection/hiding.")
    p.add_argument("--export-images", metavar="DIR", help="Save page images into DIR (P2 multimodal pointer).")
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
    bad = [u for u in urls if urlparse(u).scheme not in ("http", "https")]
    if bad:
        print(f"[gscrape] error: invalid URLs (must be http/https): {bad}", file=sys.stderr)
        sys.exit(2)
    return urls


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    urls = _collect_urls(args)
    if not urls:
        print("[gscrape] error: provide at least one URL or --urls-file", file=sys.stderr)
        return 2

    cfg = ScrapeConfig(
        output=args.output,
        delay=args.delay,
        jitter=args.jitter,
        ignore_robots=args.ignore_robots,
        cache_dir=args.cache_dir,
        max_pages=args.max_pages,
        expand=not args.no_expand,
        capture_apis=not args.no_apis,
        handle_consent=not args.no_consent,
        export_images=args.export_images,
        max_api_responses=args.max_api_responses,
        page_timeout_ms=args.page_timeout,
        headful=args.headful,
        verbose=args.verbose,
    )

    robots = RobotsPolicy(cache_dir=args.cache_dir)
    pipeline = Pipeline(cfg, robots)

    async def run_all():
        await pipeline.start()
        try:
            with JsonlWriter(cfg.output) as writer:
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
                emit_progress(cfg.verbose, f"done. wrote {writer.path}")
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


if __name__ == "__main__":
    raise SystemExit(main())