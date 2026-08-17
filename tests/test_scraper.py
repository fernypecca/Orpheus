"""Tests for the gscrape scraper.

Covers the 5 mandatory scenarios from the brief plus the P0/P1 features,
all against a deterministic LOCAL fixture server (no internet needed).
"""

import json

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