"""Battery: 20+ edge-case tests for wave-1, wave-2 and live fixes.

Pure functions only (no browser) — the whole file runs in <1s.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from growth_scraper.highlights import highlights_for_query
from growth_scraper.parse import parse_fields
from growth_scraper.documents import detect_doc_format, extract_document
from growth_scraper.pagemeta import extract_links, extract_images_info
from growth_scraper.maincontent import extract_main_text, _cap
from growth_scraper.structured import extract_structured
from growth_scraper.extractors import reconcile_page_type, run_extraction
from growth_scraper.pipeline import (
    _apply_exclude_selectors,
    _cache_fresh,
    _match_downloaded,
    _parse_max_age,
    _parse_wait_ms,
    _strip_markdown_links,
    _text_from_cleaned_html,
)
from growth_scraper.cli import _parse_headers, _parse_selectors
from growth_scraper.config import Record
from growth_scraper.utils import build_headers


# -- parse ---------------------------------------------------------------

def test_parse_list_no_match_is_empty():
    html = "<html><body><h1>T</h1></body></html>"
    assert parse_fields(html, {"prices": ".price[]"}) == {"prices": []}


def test_parse_empty_element_is_none():
    html = "<html><body><h1>   </h1></body></html>"
    assert parse_fields(html, {"title": "h1"}) == {"title": None}


def test_parse_invalid_selector_fail_open():
    html = "<html><body><p>x</p></body></html>"
    out = parse_fields(html, {"bad": "???", "ok": "p"})
    assert out["bad"] is None and out["ok"] == "x"


# -- highlights ------------------------------------------------------------

def test_highlights_respects_max_n_and_page_order():
    text = "\n\n".join(f"Bloque {i} sobre facturación electrónica." for i in range(6))
    hl = highlights_for_query(text, "facturación electrónica", max_n=2)
    assert len(hl) == 2
    assert hl[0].startswith("Bloque 0") and hl[1].startswith("Bloque 1")


def test_highlights_short_tokens_ignored():
    assert highlights_for_query("Texto largo con contenido real aquí.", "a y o", max_n=3) == []


# -- documents -------------------------------------------------------------

def test_detect_doc_mime_variants():
    base = "https://x.test/dl"
    assert detect_doc_format(base, "application/vnd.openxmlformats-officedocument.wordprocessingml.document") == "docx"
    assert detect_doc_format(base, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") == "xlsx"
    assert detect_doc_format(base, "application/vnd.openxmlformats-officedocument.presentationml.presentation") == "pptx"
    assert detect_doc_format("https://x.test/F.PDF") == "pdf"  # uppercase ext


def test_extract_pdf_blank_fail_open():
    from pypdf import PdfWriter
    import io

    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.write(buf)
    assert extract_document(buf.getvalue(), "pdf") is None


# -- pagemeta --------------------------------------------------------------

def test_links_skip_non_http_schemes():
    html = ("<html><body><a href='mailto:a@x'>m</a><a href='tel:123'>t</a>"
            "<a href='javascript:void(0)'>j</a><a href='data:x'>d</a>"
            "<a href='/ok'>Bien</a></body></html>")
    assert extract_links(html, "https://x.test/") == [
        {"url": "https://x.test/ok", "text": "Bien"}]


def test_images_srcset_and_bad_dimensions():
    html = ("<html><body><img srcset='/a-1x.png 1x, /a-2x.png 2x' alt='A'>"
            "<img src='/b.png' width='auto' height='-3'></body></html>")
    imgs = extract_images_info(html, "https://x.test/")
    assert imgs[0]["url"] == "https://x.test/a-2x.png"  # last = largest
    assert imgs[1] == {"url": "https://x.test/b.png", "alt": "",
                       "width": None, "height": None}


# -- maincontent -----------------------------------------------------------

def test_maincontent_drops_share_and_breadcrumb():
    body = "Noticia importante del día. " * 40
    html = (f"<html><body><nav class='breadcrumb'>Inicio / Noticias</nav>"
            f"<div class='share'>Compartir en redes</div>"
            f"<article><h1>Titular</h1><p>{body}</p></article></body></html>")
    main = extract_main_text(html)
    assert main and "Titular" in main
    assert "Compartir" not in main and "breadcrumb" not in main.lower()


def test_cap_cuts_at_word_boundary():
    assert _cap("aaa bbb ccc ddd", max_chars=9) == "aaa bbb"
    assert _cap("corto", max_chars=100) == "corto"


# -- structured ------------------------------------------------------------

def test_structured_microdata_product():
    html = """<html><body><div itemscope itemtype="https://schema.org/Product">
    <span itemprop="name">Taza</span><span itemprop="price">12.50</span>
    <span itemprop="priceCurrency">EUR</span><span itemprop="brand">Acme</span>
    <span itemprop="sku">TZ-1</span>
    <link itemprop="availability" href="https://schema.org/OutOfStock">
    </div></body></html>"""
    s = extract_structured(html)
    assert s and s["entityType"] == "product" and s["source"] == "microdata"
    assert s["brand"] == "Acme" and s["sku"] == "TZ-1"
    assert s["availability"] == "out_of_stock"
    assert s["price"] == {"value": "12.50", "currency": "EUR", "isRange": False}


def test_structured_heuristic_fallback():
    html = ("<html><body><h1>DJ Pedro</h1><div class='price'>€600</div>"
            "<p>Contacto: dj@ejemplo.com</p></body></html>")
    s = extract_structured(html)
    assert s and s["source"] == "heuristic"
    assert s["price"]["value"] == "€600"
    assert (s["contact"] or {}).get("email") == "dj@ejemplo.com"


def test_structured_meta_fallback_and_none():
    meta = ("<html><head><meta property='og:title' content='Banquete'>"
            "<meta name='description' content='Bodas.'></head>"
            "<body><h1>X</h1></body></html>")
    s = extract_structured(meta)
    assert s and s["source"] == "meta" and s["name"] == "Banquete"
    assert extract_structured("<html><body><p>plano</p></body></html>") is None
    assert extract_structured("") is None


def test_reconcile_branches():
    listing_struct = {"source": "jsonld", "entityType": "listing"}
    assert reconcile_page_type("generic", [{"a": 1}], listing_struct) == ("listing", [{"a": 1}])
    weak = {"source": "meta", "entityType": "profile"}
    assert reconcile_page_type("listing", [{"a": 1}], weak) == ("listing", [{"a": 1}])
    assert reconcile_page_type("generic", [], None) == ("generic", [])


# -- pipeline helpers ------------------------------------------------------

def test_strip_links_and_exclude_helpers():
    assert _strip_markdown_links("sin links") == "sin links"
    md = "A [uno](https://a.test/1) y [dos](http://b.test/2)."
    assert _strip_markdown_links(md) == "A uno y dos."
    html = "<html><body><div class='ads'>publi</div><p>real</p></body></html>"
    cleaned = _apply_exclude_selectors(html, [".ads", "???"])
    assert "publi" not in cleaned and "real" in cleaned
    assert _text_from_cleaned_html(cleaned, max_chars=4) == "real"
    assert _parse_wait_ms("abc") is None and _parse_max_age("abc") == 0.0


def test_match_downloaded_and_cache_fresh():
    import hashlib
    import os

    u1, u2 = "https://x.test/a.png", "https://x.test/b.png"
    d1 = hashlib.sha1(u1.encode()).hexdigest()[:12]
    saved = [f"/tmp/img/{d1}.png"]  # u2 failed to download
    assert _match_downloaded([u1, u2], saved) == {u1: f"/tmp/img/{d1}.png"}
    _ = os  # silence unused in some runners
    broken = Record(url="https://x.test/", scrapedAt="not-a-date")
    assert _cache_fresh(broken, 60) is True  # fail-open keeps old behavior
    assert _cache_fresh(Record(url="u"), 60) is True  # no date, no TTL


# -- cli + utils + records -------------------------------------------------

def test_cli_parsers_and_default_headers():
    assert _parse_headers(["A: 1", "sin-dos-puntos", "B:x:y"]) == {"A": "1", "B": "x:y"}
    assert _parse_selectors(["nav,.ads", " footer "]) == ["nav", ".ads", "footer"]
    h = build_headers(None)
    assert h["User-Agent"].startswith("GrowthScraperBot") and "Accept-Language" in h


def test_csv_new_columns(tmp_path):
    from growth_scraper.records import CsvWriter

    rec = Record(url="https://x.test/", title="T", text="hola mundo",
                 parsed={"a": "1"}, highlights=["h1"],
                 links=[{"url": "https://x.test/l", "text": "L"}],
                 document={"format": "pdf"})
    rec.summary = {"domain": "x.test", "wordCount": 2, "parsedKeys": ["a"],
                   "highlightsCount": 1, "linksCount": 1, "docFormat": "pdf"}
    p = tmp_path / "s.csv"
    with CsvWriter(str(p)) as w:
        w.write(rec)
    header, row = p.read_text(encoding="utf-8").splitlines()
    assert "parsedKeys" in header and "docFormat" in header
    assert "pdf" in row and "a" in row


def test_nav_role_navigation_filtered():
    from types import SimpleNamespace

    html = ("""<html><body><div role="navigation"><ul><li><a href="/n1">Uno</a></li>"""
            """<li><a href="/n2">Dos</a></li><li><a href="/n3">Tres</a></li>"""
            """<li><a href="/n4">Cuatro</a></li><li><a href="/n5">Cinco</a></li></ul></div>"""
            """<h1>Contenido</h1><p>Texto real de la página.</p></body></html>""")
    res = SimpleNamespace(cleaned_html="", html=html, url="https://x.test/")
    ptype, items = run_extraction(res)
    assert ptype != "listing" and items == []
    assert extract_links(html, "https://x.test/") == []


def test_data_uri_images_stripped():
    from growth_scraper.pipeline import _strip_data_images

    md = ("Products * [![](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)"
         "![Icon](https://x.test/i.png) real](https://x.test/p)")
    out = _strip_data_images(md)
    assert "data:image" not in out and "https://x.test/i.png" in out
    assert _strip_data_images("![Logo](data:image/png;base64,AAA)") == "Logo"
    assert _strip_data_images("plain text") == "plain text"


def test_degraded_flag_top_only():
    from growth_scraper.pipeline import _is_degraded_text

    assert _is_degraded_text("### Your browser version is not supported. Try apps!") is True
    long_ok = "Contenido. " * 500 + "Some say please upgrade your browser for fun."
    assert _is_degraded_text(long_ok) is False  # mention buried deep = prose
    assert _is_degraded_text("") is False


def test_items_skip_loading_and_bare_controls():
    from growth_scraper.extractors import extract_items
    from bs4 import BeautifulSoup

    html = """<html><body><ul>
    <li>Generating your product picks...</li>
    <li><span>Expand</span></li>
    <li><a href="/p">Real feature</a></li>
    <li>Basic AI search</li>
    </ul></body></html>"""
    items = extract_items(BeautifulSoup(html, "html.parser"), "https://x.test/")
    titles = [i["title"] for i in items]
    assert titles == ["Real feature", "Basic AI search"], titles
