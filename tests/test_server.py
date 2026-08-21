"""Tests for `gscrape serve` (warm-browser HTTP server)."""

from fastapi.testclient import TestClient
import pytest

from conftest import base_cfg
from growth_scraper.server import check_token, create_app


def _base_cfg():
    return base_cfg(page_timeout_ms=15000)


@pytest.fixture(scope="session")
def client():
    # allow_private_targets=True: these tests scrape the local fixture server
    # (loopback), which the SSRF guard would otherwise correctly reject.
    app = create_app(_base_cfg(), allow_private_targets=True)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["version"]


def test_scrape_ok(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/")})
    assert r.status_code == 200
    rec = r.json()
    assert rec["url"] == fs.url("/")
    assert "contenido principal" in rec["text"]
    assert rec["error"] is None


def test_scrape_protection_passthrough(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/blocked")})
    assert r.status_code == 200
    rec = r.json()
    assert rec["protectionBlocked"] is True
    assert rec["error"].startswith("PROTECTION_BLOCKED")
    assert rec["text"] == ""


def test_scrape_robots_record(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/private")})
    assert r.status_code == 200
    assert r.json()["error"].startswith("ROBOTS_BLOCKED")


def test_scrape_invalid_body(client):
    r = client.post("/scrape", json={})
    assert r.status_code == 400


def test_scrape_non_http_url(client):
    r = client.post("/scrape", json={"url": "ftp://example.com/x"})
    assert r.status_code == 400


def test_scrape_cache_fastpath(client, fs, tmp_path):
    cache_dir = str(tmp_path / "cache")
    before = fs.state.hits.get("/", 0)
    r1 = client.post("/scrape", json={"url": fs.url("/"),
                                      "options": {"cacheDir": cache_dir}})
    assert r1.status_code == 200
    assert r1.json().get("fromCache") is not True
    r2 = client.post("/scrape", json={"url": fs.url("/"),
                                      "options": {"cacheDir": cache_dir}})
    assert r2.status_code == 200
    assert r2.json()["fromCache"] is True
    assert fs.state.hits["/"] == before + 1


def test_scrape_options_respected(client, fs):
    r = client.post("/scrape", json={"url": fs.url("/"),
                                     "options": {"maxTextChars": 40}})
    assert r.status_code == 200
    rec = r.json()
    assert rec["error"] is None
    assert 0 < len(rec["text"]) <= 40


def test_check_token_unit():
    check_token(None, "Bearer whatever")  # no token configured -> always ok
    check_token("secret", "Bearer secret")
    with pytest.raises(Exception):
        check_token("secret", "Bearer nope")
    with pytest.raises(Exception):
        check_token("secret", None)