"""Fase 4 coverage tests: consent gating, cross-origin iframes, anti-bot retry."""


def test_record_to_dict_coverage_fields():
    from growth_scraper.config import Record

    r = Record(url="https://example.com")
    d = r.to_dict()
    assert d["frames"] is None
    assert d["frameTexts"] is None
    assert d["retries"] == 0

    r.frames = [{"src": "https://x.com/f", "crossOrigin": True}]
    r.frameTexts = [{"src": "https://x.com/f", "text": "hola"}]
    r.retries = 2
    d = r.to_dict()
    assert d["frames"][0]["crossOrigin"] is True
    assert d["frameTexts"][0]["text"] == "hola"
    assert d["retries"] == 2


def test_scrape_config_fase4_defaults():
    from growth_scraper.config import ScrapeConfig

    c = ScrapeConfig()
    assert c.consent_wait_ms == 5000
    assert c.fetch_frames is True
    assert c.max_frames == 5
    assert c.anti_bot_retries == 2
    assert c.anti_bot_backoff_s == 15.0