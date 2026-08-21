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
    assert by_src["http://127.0.0.1:9/content"]["crossOrigin"] is True
    texts = {t["src"]: t["text"] for t in rec.frameTexts}
    assert "Texto del iframe local" in texts[fs.url("/local-frame")]
    assert "Error:" in texts["http://127.0.0.1:9/content"]


def test_e2e_frames_skipped_when_disabled(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(fetch_frames=False), fs.url("/frames")))
    assert rec.frames is None
    assert rec.frameTexts is None


def test_e2e_frames_redirect_and_robots(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(fetch_frames=True), fs.url("/frames-robots")))
    texts = {t["src"]: t["text"] for t in rec.frameTexts}
    assert texts[fs.url("/private")] == "Skipped by robots"
    assert "Texto del iframe local" in texts[fs.url("/redirect")]


def test_e2e_redirect_captures_final_url(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(), fs.url("/redirect")))
    assert rec.statusCode == 200
    assert rec.finalUrl == fs.url("/local-frame")
    assert "Texto del iframe local" in rec.text


def test_e2e_frame_title_fallback_and_tracker_filter(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(fetch_frames=True), fs.url("/frames-title")))
    assert len(rec.frames) == 1
    assert "googletagmanager.com" not in rec.frames[0]["src"]
    assert rec.frameTexts[0]["text"] == "Mi Video Embed"


def test_needs_wait_cases():
    from growth_scraper.waitcontent import needs_wait

    assert needs_wait("") is False
    assert needs_wait("no-consent-found") is False
    assert needs_wait("error") is False
    assert needs_wait("rejected") is True
    assert needs_wait("hidden:1") is True
    assert needs_wait("removed:2") is True


def test_wait_for_content_off():
    import asyncio

    from growth_scraper.config import ScrapeConfig
    from growth_scraper.waitcontent import wait_for_content

    assert asyncio.run(wait_for_content(None, ScrapeConfig(consent_wait_ms=0))) == "off"


def test_wait_for_content_plateau():
    import asyncio

    from growth_scraper.config import ScrapeConfig
    from growth_scraper.waitcontent import wait_for_content

    class FakePage:
        async def evaluate(self, _expr):
            return 100

    summary = asyncio.run(wait_for_content(FakePage(), ScrapeConfig(consent_wait_ms=5000)))
    assert summary.startswith("wait ")
    assert "delta" in summary


def test_extract_iframes_cap_after_filter():
    from growth_scraper.iframes import extract_iframes

    html = "".join(f'<iframe src="data:text/html,{i}"></iframe>' for i in range(5))
    html += '<iframe src="/real" title="Real"></iframe>'
    frames = extract_iframes("https://site.com/p", html, max_frames=5)
    assert len(frames) == 1
    assert frames[0]["src"] == "https://site.com/real"


def test_extract_iframes_skips_non_http_schemes():
    from growth_scraper.iframes import extract_iframes

    html = ('<iframe src="mailto:x@y.com"></iframe>'
            '<iframe src="tel:+3491"></iframe>'
            '<iframe src="file:///etc/passwd"></iframe>'
            '<iframe src="https://ok.com/f"></iframe>')
    frames = extract_iframes("https://site.com/p", html, max_frames=5)
    assert len(frames) == 1
    assert frames[0]["src"] == "https://ok.com/f"


def test_csv_retries_column(tmp_path):
    import csv

    from growth_scraper.config import Record
    from growth_scraper.records import CsvWriter

    out = tmp_path / "out.csv"
    with CsvWriter(str(out)) as w:
        rec = Record(url="https://x.com")
        rec.retries = 2
        w.write(rec)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    header = rows[0]
    assert "retries" in header
    assert rows[1][header.index("retries")] == "2"


def test_protection_detect_bare_429():
    from growth_scraper import protection

    class Stub:
        status_code = 429
        html = "<html><body>Too many requests</body></html>"
        metadata = {"title": "Rate limited"}
        response_headers = {"server": "nginx"}

    reason = protection.detect(Stub())
    assert reason and reason.startswith("PROTECTION_BLOCKED")
    assert "429" in reason


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


# -- pageType/structured reconciliation --------------------------------------
# Regression case: bodas.net vendor profiles have a busy header nav + photo
# carousel (>=4 <li>) but no <dt>/itemprop/<address>, so classify() alone
# always misreads them as "listing" — verified live. structured.py reads the
# same page's real LocalBusiness JSON-LD correctly; when it does, it must win.

def test_reconcile_prefers_trusted_structured_profile_over_listing_heuristic():
    from growth_scraper.extractors import reconcile_page_type

    junk_items = [{"title": "Accede", "href": "https://x/login", "snippet": "Accede"}]
    page_type, items = reconcile_page_type(
        "listing", junk_items, {"entityType": "profile", "source": "jsonld"}
    )
    assert page_type == "profile"
    assert items == []


def test_reconcile_ignores_untrusted_structured_source():
    from growth_scraper.extractors import reconcile_page_type

    page_type, items = reconcile_page_type(
        "listing", [{"title": "x"}], {"entityType": "profile", "source": "heuristic"}
    )
    assert page_type == "listing"
    assert items == [{"title": "x"}]


def test_reconcile_noop_when_types_agree_or_structured_missing():
    from growth_scraper.extractors import reconcile_page_type

    assert reconcile_page_type("profile", [], {"entityType": "profile", "source": "jsonld"}) == ("profile", [])
    assert reconcile_page_type("generic", [], None) == ("generic", [])


# -- server SSRF guard --------------------------------------------------------

def test_ssrf_guard_blocks_loopback_and_metadata_and_private_ranges():
    from growth_scraper.server import _is_ssrf_target

    assert _is_ssrf_target("http://127.0.0.1/") is True
    assert _is_ssrf_target("http://169.254.169.254/opc/v1/instance/") is True  # cloud metadata
    assert _is_ssrf_target("http://10.0.0.5/") is True
    assert _is_ssrf_target("http://192.168.1.1/") is True


def test_ssrf_guard_allows_public_ip():
    from growth_scraper.server import _is_ssrf_target

    assert _is_ssrf_target("http://8.8.8.8/") is False


def test_ssrf_guard_blocks_unresolvable_host():
    from growth_scraper.server import _is_ssrf_target

    assert _is_ssrf_target("http://this-host-should-not-resolve.invalid/") is True


def test_server_scrape_rejects_loopback_by_default(fs):
    from fastapi.testclient import TestClient
    from conftest import base_cfg
    from growth_scraper.server import create_app

    # No allow_private_targets here — this is the real, deployed default.
    app = create_app(base_cfg())
    with TestClient(app) as client:
        r = client.post("/scrape", json={"url": fs.url("/")})
    assert r.status_code == 400