"""Tests for the gscrape scraper.

Covers the 5 mandatory scenarios from the brief plus the P0/P1 features,
all against a deterministic LOCAL fixture server (no internet needed).
"""

import json
import os

from conftest import base_cfg, run, scrape_url


# ---------------------------------------------------------------------------
# Scenario 1 — simple site, JSONL well formed
# ---------------------------------------------------------------------------
def test_scenario1_simple_site(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/")))
    d = rec.to_dict()
    assert d["error"] is None
    assert d["statusCode"] == 200
    assert d["title"] == "Acme Simple Page"
    assert "contenido principal de la página simple" in d["text"]
    # no nav / footer / script noise
    assert "Footer Content" not in d["text"]
    assert "Inicio" not in d["text"]
    assert "no debe salir" not in d["text"]
    # contract fields present
    for field in ("url", "title", "text", "apiResponses"):
        assert field in d
    assert isinstance(d["apiResponses"], list)


def test_scenario1_cli_jsonl(fs, tmp_path):
    """End-to-end via the CLI: output file is valid JSONL, one record per line."""
    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    code = main([fs.url("/"), "-o", str(out), "--delay", "0", "--jitter", "0"])
    assert code == 0
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["url"] == fs.url("/")
    assert rec["title"] == "Acme Simple Page"
    assert "contenido principal" in rec["text"]


# ---------------------------------------------------------------------------
# Scenario 2 — strong cookie banner: zero noise in text
# ---------------------------------------------------------------------------
def test_scenario2_consent_banner_no_noise(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/cookie")))
    d = rec.to_dict()
    low = d["text"].lower()
    assert "El contenido real que interesa al scraper" in d["text"]
    for banned in ("consentimiento", "propósitos", "iab", "rechazar", "aceptar todo", "marketing"):
        assert banned not in low, f"banner word leaked: {banned}"
    # "Aceptar todo" must never have been clicked
    assert fs.state.hits.get("/consent-accepted", 0) == 0


# ---------------------------------------------------------------------------
# Scenario 3 — crawl mode: respects page cap and stays in-domain
# ---------------------------------------------------------------------------
def test_scenario3_crawl_cap_and_domain(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "crawl.jsonl"
    code = main(
        [fs.url("/loop"), "--crawl", "--max-pages", "3", "-o", str(out),
         "--delay", "0", "--jitter", "0"]
    )
    assert code == 0
    records = [json.loads(ln) for ln in out.read_text().strip().splitlines()]
    assert len(records) == 3, f"expected 3 records, got {len(records)}"
    fixture_host = fs.url("/").split("://")[1].split(":")[0]
    for r in records:
        host = r["url"].split("://")[1].split(":")[0]
        assert host == fixture_host, f"escaped domain: {r['url']}"
    # robots.txt disallows /private and example.test is external
    urls = [r["url"] for r in records]
    assert not any("/private" in u for u in urls)
    assert not any("example.test" in u for u in urls)


# ---------------------------------------------------------------------------
# Scenario 4 — strong protection: fail closed, clear and explicit
# ---------------------------------------------------------------------------
def test_scenario4_protection_fails_closed(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/blocked")))
    d = rec.to_dict()
    assert d["protectionBlocked"] is True
    assert d["error"].startswith("PROTECTION_BLOCKED")
    assert d["text"] == ""
    assert d["apiResponses"] == []


# ---------------------------------------------------------------------------
# Scenario 5 — cache: second run does not reprocess
# ---------------------------------------------------------------------------
def test_scenario5_cache_no_reprocess(fs, tmp_path):
    cache_dir = str(tmp_path / "cache")
    cfg = base_cfg(cache_dir=cache_dir)
    url = fs.url("/")
    before = fs.state.hits["/"]

    rec1 = run(scrape_url(cfg, url))
    rec2 = run(scrape_url(cfg, url))

    assert fs.state.hits["/"] == before + 1, "second run must not hit the network"
    assert rec2.url == rec1.url
    assert rec2.text == rec1.text


# ---------------------------------------------------------------------------
# P0 — collapsed content + XHR-backed FAQ
# ---------------------------------------------------------------------------
def test_p0_collapse_and_xhr(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/faq")))
    d = rec.to_dict()
    # <details> opened without clicks
    assert "Enviamos en 24 horas a toda España" in d["text"]
    assert "Tienes 30 días para devolver" in d["text"]
    # aria-accordion expanded
    assert "Requisitos: DNI y tarjeta" in d["text"]
    # XHR-triggered content (the real P0 case)
    assert "49 euros al mes" in d["text"]
    assert "prueba gratuita" in d["text"]
    # the internal API response was captured
    faq = [a for a in d["apiResponses"] if a["url"].endswith("/api/faq")]
    assert faq, "expected /api/faq in apiResponses"
    assert len(faq[0]["body"]) == 2
    # blacklisted button and cookie-container button never clicked
    assert fs.state.hits.get("/purchased", 0) == 0
    assert fs.state.hits.get("/cookie-expanded", 0) == 0


# ---------------------------------------------------------------------------
# P1 — API pagination replay
# ---------------------------------------------------------------------------
def test_p1_api_pagination_replay(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/paginated")))
    d = rec.to_dict()
    api_urls = [a["url"] for a in d["apiResponses"]]
    assert any("page=1" in u for u in api_urls)
    assert any("page=2" in u for u in api_urls), "replay should fetch page=2"
    # page=3 is empty (end of pagination) and must not be stored; no duplicates
    assert not any("page=3" in u for u in api_urls)
    assert not any("page=4" in u for u in api_urls)
    assert len(api_urls) == len(set(api_urls)), "duplicate API URLs"


# ---------------------------------------------------------------------------
# P1 — page-type extractors
# ---------------------------------------------------------------------------
def test_p1_listing_extractor(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/listing")))
    d = rec.to_dict()
    assert d["pageType"] == "listing"
    assert len(d["items"]) >= 4
    assert all(i.get("title") and i.get("href") for i in d["items"])


def test_p1_profile_extractor(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/profile")))
    d = rec.to_dict()
    assert d["pageType"] == "profile"
    assert len(d["items"]) == 1
    profile = d["items"][0]
    assert profile.get("title") == "Proveedor 3"
    assert profile.get("fields", {}).get("País") == "España"
    assert "hola@proveedor3.com" in profile.get("emails", [])


# ---------------------------------------------------------------------------
# Click guard — blacklist + cookie-container exclusion
# ---------------------------------------------------------------------------
def test_guard_blacklist_and_containers(fs):
    cfg = base_cfg(handle_consent=False)  # keep the fake banner in the DOM
    rec = run(scrape_url(cfg, fs.url("/guard")))
    d = rec.to_dict()
    assert "Contenido real visible" in d["text"]
    # "Comprar ahora" (buy now) must never be auto-clicked
    assert fs.state.hits.get("/purchased", 0) == 0
    # expander inside a cookie container must never be clicked
    assert fs.state.hits.get("/cookie-expanded", 0) == 0


# ---------------------------------------------------------------------------
# robots.txt — respected by default, bypassable
# ---------------------------------------------------------------------------
def test_robots_respected_by_default(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/private")))
    assert rec.error and rec.error.startswith("ROBOTS_BLOCKED")
    assert rec.text == ""


def test_robots_ignore_flag(fs):
    rec = run(scrape_url(base_cfg(ignore_robots=True), fs.url("/private")))
    assert rec.error is None
    assert "contenido es privado" in rec.text


# ---------------------------------------------------------------------------
# URL normalization (P0): tracking params are stripped for dedupe and records
# ---------------------------------------------------------------------------
def test_url_normalization_unit():
    from growth_scraper.urlutil import normalize_url

    assert normalize_url("HTTP://Example.COM/path?utm_source=x&a=1#sec") == "http://example.com/path?a=1"
    assert normalize_url("https://x.com/p?fbclid=1&gclid=2&q=hola") == "https://x.com/p?q=hola"
    assert normalize_url("https://x.com/p?q=hola&page=2") == "https://x.com/p?q=hola&page=2"


def test_crawl_dedupes_tracking_params(fs, tmp_path):
    """Two links to the same page with different tracking params -> one record, clean URL."""
    from growth_scraper.cli import main

    out = tmp_path / "crawl_utm.jsonl"
    code = main(
        [fs.url("/looputm"), "--crawl", "--max-pages", "4", "--concurrency", "2",
         "-o", str(out), "--delay", "0", "--jitter", "0"]
    )
    assert code == 0
    records = [json.loads(ln) for ln in out.read_text().strip().splitlines()]
    assert len(records) == 3, f"tracking duplicates must dedupe, got {len(records)}"
    for r in records:
        assert "utm_" not in r["url"] and "fbclid" not in r["url"]
    assert any(r["url"].endswith("/loop2") for r in records)


def test_crawl_concurrency(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "crawl_conc.jsonl"
    code = main(
        [fs.url("/loop"), "--crawl", "--max-pages", "3", "--concurrency", "2",
         "-o", str(out), "--delay", "0", "--jitter", "0"]
    )
    assert code == 0
    records = [json.loads(ln) for ln in out.read_text().strip().splitlines()]
    assert len(records) == 3
    assert any(r["url"].endswith("/loop2") for r in records)
    assert any(r["url"].endswith("/loop3") for r in records)


# ---------------------------------------------------------------------------
# Sitemap seed (P1): --sitemap seeds the crawl from sitemap.xml
# ---------------------------------------------------------------------------
def test_sitemap_seed(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "sitemap.jsonl"
    code = main(
        ["--sitemap", fs.url("/sitemap.xml"), "-o", str(out),
         "--delay", "0", "--jitter", "0"]
    )
    assert code == 0
    records = [json.loads(ln) for ln in out.read_text().strip().splitlines()]
    urls = [r["url"] for r in records]
    assert any(u.endswith("/") and u.count("/") == 3 for u in urls)  # the home page
    assert any(u.endswith("/loop2") for u in urls)
    assert not any("/private" in u for u in urls), "robots-disallowed must be filtered"


# ---------------------------------------------------------------------------
# LLM-ready text (P1): --max-text-chars caps records for small TPM tiers
# ---------------------------------------------------------------------------
def test_max_text_chars(fs):
    rec = run(scrape_url(base_cfg(max_text_chars=40), fs.url("/")))
    d = rec.to_dict()
    assert d["error"] is None
    assert 0 < len(d["text"]) <= 40
    assert d["summary"]["wordCount"] == len(d["text"].split())


# ---------------------------------------------------------------------------
# Retry with backoff (P2): transient 5xx is retried
# ---------------------------------------------------------------------------
def test_retry_transient_5xx(fs):
    url = fs.url("/flaky")
    before = fs.state.hits["/flaky"]
    rec = run(scrape_url(base_cfg(), url))
    assert rec.error is None
    assert rec.statusCode == 200
    assert fs.state.hits["/flaky"] == before + 2, "500 then 200 = 2 hits"


def test_protection_detect_empty_stalled_page():
    """No HTTP status + empty payload = fail closed (demo.datadome.co case)."""
    from growth_scraper import protection

    class Stub:
        status_code = None
        html = ""
        metadata = {}
        response_headers = {}

    reason = protection.detect(Stub())
    assert reason and reason.startswith("PROTECTION_BLOCKED")
    assert "empty payload" in reason


# ---------------------------------------------------------------------------
# Image export (P2): extracted from raw HTML, downloaded to the export dir
# ---------------------------------------------------------------------------
def test_image_export_extracts_and_downloads(fs, tmp_path):
    from growth_scraper.cli import main

    img_dir = tmp_path / "assets"
    out = tmp_path / "img.jsonl"
    code = main([fs.url("/withimg"), "-o", str(out), "--export-images", str(img_dir),
                 "--delay", "0", "--jitter", "0"])
    assert code == 0
    rec = json.loads(out.read_text().strip())
    assert len(rec["images"]) == 2, f"src + data-src both extracted, got {rec['images']}"
    for path in rec["images"]:
        assert os.path.exists(path)


def test_extract_image_urls_unit():
    from growth_scraper.pipeline import _extract_image_urls

    html = (
        '<img src="/a.png"><img data-src="/b.jpg"><img srcset="/c-small.png 1x, /c-big.png 2x">'
    )
    urls = _extract_image_urls("https://x.com/p", html, limit=10)
    assert urls == [
        "https://x.com/a.png",
        "https://x.com/b.jpg",
        "https://x.com/c-big.png",
    ]


# ---------------------------------------------------------------------------
# Summary field (P2): cheap structured triage data on every record
# ---------------------------------------------------------------------------
def test_summary_field(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/profile")))
    s = rec.to_dict()["summary"]
    assert s["domain"] == "127.0.0.1"
    assert s["title"] == "Proveedor 3"
    assert s["pageType"] == "profile"
    assert s["itemCount"] == 1
    assert s["wordCount"] > 0


# ---------------------------------------------------------------------------
# CSV export (P2): --csv writes a flat summary next to the .jsonl
# ---------------------------------------------------------------------------
def test_csv_output(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    code = main([fs.url("/"), "-o", str(out), "--csv", "--delay", "0", "--jitter", "0"])
    assert code == 0
    csv_path = tmp_path / "cli.csv"
    assert csv_path.exists()
    lines = csv_path.read_text().strip().splitlines()
    assert lines[0].startswith("url,title,pageType,domain")
    assert len(lines) == 2  # header + 1 record
    assert "Acme Simple Page" in lines[1]