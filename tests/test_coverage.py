"""Fase 4 coverage tests: consent gating, cross-origin iframes, anti-bot retry."""


def test_e2e_gated_content_with_wait(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(consent_wait_ms=3000), fs.url("/gated")))
    assert "Contenido adicional que carga tras rechazar" in rec.text


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


def test_extract_iframes_pure():
    from growth_scraper.iframes import extract_iframes

    html = """<iframe src="https://videos.com/embed/x" title="Vídeo"></iframe>
<iframe src="/local" title="Local"></iframe>
<iframe src="data:text/html;base64,abc"></iframe>
<iframe></iframe>"""
    frames = extract_iframes("https://site.com/page", html, max_frames=5)
    assert len(frames) == 2
    assert frames[0]["src"] == "https://videos.com/embed/x"
    assert frames[0]["title"] == "Vídeo"
    assert frames[0]["crossOrigin"] is True
    assert frames[1]["src"] == "https://site.com/local"
    assert frames[1]["crossOrigin"] is False


def test_e2e_frames_reported(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(fetch_frames=True), fs.url("/frames")))
    assert rec.frames and len(rec.frames) == 2
    by_src = {f["src"]: f for f in rec.frames}
    assert by_src[fs.url("/local-frame")]["crossOrigin"] is False
    assert by_src["https://cross-frame.test/content"]["crossOrigin"] is True
    texts = {t["src"]: t["text"] for t in rec.frameTexts}
    assert "Texto del iframe local" in texts[fs.url("/local-frame")]
    assert "Error:" in texts["https://cross-frame.test/content"]


def test_e2e_frames_skipped_when_disabled(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(fetch_frames=False), fs.url("/frames")))
    assert rec.frames is None
    assert rec.frameTexts is None


def test_e2e_anti_bot_retry_succeeds(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(
        base_cfg(max_retries=0, anti_bot_retries=2, anti_bot_backoff_s=0.05, jitter=0),
        fs.url("/flaky403?fails=2"),
    ))
    assert rec.statusCode == 200
    assert rec.retries == 2
    assert rec.protectionBlocked is False


def test_e2e_anti_bot_retry_blocked(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(
        base_cfg(max_retries=0, anti_bot_retries=0),
        fs.url("/flaky403?fails=999"),
    ))
    assert rec.protectionBlocked is True
    assert rec.retries == 0


def test_scrape_config_fase4_defaults():
    from growth_scraper.config import ScrapeConfig

    c = ScrapeConfig()
    assert c.consent_wait_ms == 5000
    assert c.fetch_frames is True
    assert c.max_frames == 5
    assert c.anti_bot_retries == 2
    assert c.anti_bot_backoff_s == 15.0