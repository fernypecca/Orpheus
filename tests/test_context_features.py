"""Context.dev-inspired local features: parse, highlights, content controls.

Pure-function tests only (no browser) so they stay fast and deterministic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from growth_scraper.parse import parse_fields, parse_rules_from_cli
from growth_scraper.highlights import highlights_for_query
from growth_scraper.pipeline import (
    _apply_exclude_selectors,
    _parse_wait_ms,
    _strip_markdown_links,
    _text_from_cleaned_html,
)
from growth_scraper.utils import build_headers
from growth_scraper.config import ScrapeConfig

HTML = """<!doctype html><html><head><title>T</title></head><body>
<nav>Nav noise</nav>
<h1>Acme Simple Page</h1>
<p>Este es el contenido principal sobre envíos en 24 horas a toda España.</p>
<p class="ads">Oferta especial solo hoy.</p>
<p>Devoluciones en 30 días sin preguntas.</p>
<footer>Footer noise</footer>
</body></html>"""


def test_parse_single_and_list():
    rules = {"title": "h1", "paras": "p[]", "missing": ".nope"}
    out = parse_fields(HTML, rules)
    assert out["title"] == "Acme Simple Page"
    assert isinstance(out["paras"], list) and len(out["paras"]) >= 3
    assert out["missing"] is None


def test_parse_rules_from_cli():
    rules = parse_rules_from_cli(["title=h1", "prices=.price[]", "bad-no-equals"])
    assert rules == {"title": "h1", "prices": ".price[]"}


def test_parse_fail_open():
    assert parse_fields("", {"a": "h1"}) == {}
    assert parse_fields(HTML, None) == {}
    assert parse_fields(HTML, {}) == {}


def test_highlights_query():
    text = (
        "Enviamos en 24 horas a toda España.\n\n"
        "Tienes 30 días para devolver.\n\n"
        "Nuestra oficina está en Madrid y abre los lunes."
    )
    hl = highlights_for_query(text, "envío 24 horas España", max_n=2)
    assert len(hl) >= 1
    assert any("24 horas" in h for h in hl)


def test_highlights_fail_open():
    assert highlights_for_query("", "q") == []
    assert highlights_for_query("algo de texto largo aquí", "") == []
    assert highlights_for_query("texto", "q", max_n=0) == []


def test_exclude_selectors():
    cleaned = _apply_exclude_selectors(HTML, ["nav", "footer", ".ads"])
    assert "Nav noise" not in cleaned
    assert "Footer noise" not in cleaned
    assert "Oferta especial" not in cleaned
    assert "Acme Simple Page" in cleaned
    text = _text_from_cleaned_html(cleaned)
    assert "Nav noise" not in text and "contenido principal" in text


def test_strip_markdown_links():
    md = "Ver [precios](https://acme.test/pricing) y [docs](https://acme.test/docs)."
    assert _strip_markdown_links(md) == "Ver precios y docs."


def test_parse_wait_ms():
    assert _parse_wait_ms("1200") == 1200
    assert _parse_wait_ms("500ms") == 500
    assert _parse_wait_ms("2s") == 2000
    assert _parse_wait_ms("main") is None
    assert _parse_wait_ms(None) is None


def test_headers_honor_overrides():
    cfg = ScrapeConfig(extra_headers={"X-Custom": "1"}, accept_language="es-ES,es;q=0.9")
    h = build_headers(cfg)
    assert h["X-Custom"] == "1"
    assert h["Accept-Language"] == "es-ES,es;q=0.9"
    assert h["X-Crawl4AI-Untouched"] == "1"


def test_config_defaults_keep_old_behavior():
    cfg = ScrapeConfig()
    assert cfg.parse_rules == {}
    assert cfg.query is None
    assert cfg.include_links is True
    assert cfg.exclude_selectors == []
    assert cfg.wait_for is None
