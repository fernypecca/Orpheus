"""robots.txt policy. Respected by default; --ignore-robots bypasses.

Uses stdlib urllib.robotparser, cached per domain in memory plus an optional
on-disk cache. Fail-open on fetch errors (we cannot know the rules) but log a
warning so the behaviour is visible.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import httpx

from .config import ROBOTS_CACHE_TTL_SECONDS, ROBOTS_UA_TOKEN

_MAX_ROBOTS_BYTES = 2_000_000
_MISSING = object()  # sentinel: no disk cache entry


class RobotsPolicy:
    def __init__(self, cache_dir: str | None = None):
        self._memory: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._crawl_delay: dict[str, float] = {}
        self._cache_dir = cache_dir
        self._lock = asyncio.Lock()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # -- public API ---------------------------------------------------------
    async def is_allowed(self, url: str) -> bool:
        rp = await self._parser_for(url)
        if rp is None:
            return True  # no robots.txt -> allowed
        return rp.can_fetch("GrowthScraperBot", url) or rp.can_fetch("*", url)

    async def crawl_delay(self, url: str) -> float | None:
        await self._parser_for(url)
        domain = _domain_of(url)
        return self._crawl_delay.get(domain)

    # -- internals -----------------------------------------------------------
    async def _parser_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        domain = _domain_of(url)
        async with self._lock:
            if domain in self._memory:
                return self._memory[domain]
        rp = await asyncio.to_thread(self._load, url, domain)
        async with self._lock:
            if domain not in self._memory:
                self._memory[domain] = rp
            return self._memory[domain]

    def _load(self, url: str, domain: str) -> urllib.robotparser.RobotFileParser | None:
        rp = urllib.robotparser.RobotFileParser()
        # disk cache first
        cached = self._read_disk(domain)
        if cached is not _MISSING:
            if isinstance(cached, dict):
                self._crawl_delay[domain] = cached.get("crawl_delay") or 0.0
                rp.parse(cached["rules"])
                return rp
            return cached  # None marker -> no robots.txt

        robots_url = urljoin(url, "/robots.txt")
        try:
            # fresh client per fetch: this runs inside a thread pool
            with httpx.Client(follow_redirects=True, timeout=10, headers={"User-Agent": ROBOTS_UA_TOKEN}) as client:
                resp = client.get(robots_url)
            body = resp.text[: _MAX_ROBOTS_BYTES] if resp.status_code < 400 else ""
            if resp.status_code >= 400:
                rp = None  # 404/403 -> no rules, allow everything
                self._write_disk(domain, None)
                return rp
        except Exception:
            print(f"[gscrape] warning: could not fetch {robots_url} -> allowing", flush=True)
            self._write_disk(domain, None)
            return None

        rp.parse(body.splitlines())
        delay = _extract_crawl_delay(body)
        self._crawl_delay[domain] = delay
        self._write_disk(domain, {"rules": body.splitlines(), "crawl_delay": delay})
        return rp

    # -- disk cache ----------------------------------------------------------
    def _cache_path(self, domain: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", domain)
        return os.path.join(self._cache_dir, f"robots-{safe}.json") if self._cache_dir else ""

    def _read_disk(self, domain: str):
        if not self._cache_dir:
            return _MISSING
        path = self._cache_path(domain)
        try:
            if not os.path.exists(path):
                return _MISSING
            age = time.time() - os.path.getmtime(path)
            if age > ROBOTS_CACHE_TTL_SECONDS:
                return _MISSING
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return _MISSING

    def _write_disk(self, domain: str, value) -> None:
        if not self._cache_dir:
            return
        try:
            with open(self._cache_path(domain), "w", encoding="utf-8") as f:
                json.dump(value, f)
        except Exception:
            pass


def _domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def _extract_crawl_delay(body: str) -> float:
    m = re.search(r"(?im)^crawl-delay:\s*([\d.]+)\s*$", body)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0