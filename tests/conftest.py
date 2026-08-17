import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fixtureserver import FixtureServer  # noqa: E402
from growth_scraper.config import ScrapeConfig  # noqa: E402
from growth_scraper.pipeline import Pipeline  # noqa: E402
from growth_scraper.robots import RobotsPolicy  # noqa: E402


@pytest.fixture(scope="session")
def fs():
    with FixtureServer() as server:
        yield server


def base_cfg(**overrides) -> ScrapeConfig:
    values = dict(delay=0.0, jitter=0.0, page_timeout_ms=15000)
    values.update(overrides)
    return ScrapeConfig(**values)


async def scrape_url(cfg: ScrapeConfig, url: str):
    pipeline = Pipeline(cfg, RobotsPolicy(cache_dir=cfg.cache_dir))
    await pipeline.start()
    try:
        return await pipeline.run_one(url)
    finally:
        await pipeline.close()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def out_path(tmp_path):
    return tmp_path / "out.jsonl"