"""Polite pacing between page loads."""

from __future__ import annotations

import asyncio
import random

from .config import ScrapeConfig


class Pacer:
    def __init__(self, cfg: ScrapeConfig):
        self.cfg = cfg

    async def wait(self, crawl_delay: float | None = None) -> None:
        await asyncio.sleep(self.cfg.polite_delay(crawl_delay))

    @staticmethod
    def jittered(delay: float, jitter: float) -> float:
        return delay + random.uniform(0, jitter)