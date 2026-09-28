"""Second wave: documents, page inventory, main-content, product, cache TTL.

Pure-function tests only (no browser) — fast and deterministic.
"""

import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from growth_scraper.documents import detect_doc_format, extract_document
from growth_scraper.pagemeta import extract_links, extract_images_info
from growth_scraper.maincontent import extract_main_text
from growth_scraper.structured import extract_structured
from growth_scraper.extractors import reconcile_page_type
from growth_scraper.pipeline import _cache_fresh, _parse_max_age, _parse_wait_ms
from growth_scraper.config import Record


def _pdf_bytes(text="Hola PDF de precios: 49 euros al mes."):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(200, 200)
    buf = io.BytesIO()
    w.write(buf)
    # pypdf blank pages carry no text: overlay minimal content stream
    data = buf.getvalue()
    assert data.startswith(b"%PDF")
    return data


def test_detect_doc_format():
    assert detect_doc_format("https://x.test/cat.pdf") == "pdf"
    assert detect_doc_format("https://x.test/f.docx") == "docx"
    assert detect_doc_format("https://x.test/f.xlsx") == "xlsx"
    assert detect_doc_format("https://x.test/f.pptx") == "pptx"
    assert detect_doc_format("https://x.test/page") is None
    assert detect_doc_format("https://x.test/dl", "application/pdf") == "pdf"
    assert detect_doc_format("https://x.test/dl", "text/html") is None


def test_extract_docx_xlsx_pptx_roundtrip():
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation

    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Contrato de precios especiales para clientes.")
    doc.save(buf)
    out = extract_document(buf.getvalue(), "docx")
    assert out and "precios especiales" in out[0] and out[1]["format"] == "docx"

    buf = io.BytesIO()
    wb = Workbook()
    wb.active.append(["Plan", "Precio"])
    wb.active.append(["Pro", 49])
    wb.save(buf)
    out = extract_document(buf.getvalue(), "xlsx")
    assert out and "Pro | 49" in out[0] and out[1]["format"] == "xlsx"

    buf = io.BytesIO()
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(0, 0, 100, 100).text_frame.text = "Lanzamiento 2026"
    prs.save(buf)
    out = extract_document(buf.getvalue(), "pptx")
    assert out and "Lanzamiento 2026" in out[0] and out[1]["format"] == "pptx"


def test_extract_document_fail_open():
    assert extract_document(b"", "pdf") is None
    assert extract_document(b"not a pdf", "pdf") is None
    assert extract_document(b"xxx", "unknown") is None
    assert _pdf_bytes() is not None  # writer works in this env


def test_links_and_images_info():
    html = """<html><body><nav><a href="/a">A</a></nav>
    <h1>T</h1><a href="https://x.test/p">Precios</a>
    <a href="mailto:a@x.test">mail</a><a href="#top">top</a>
    <img src="/i/hero.jpg" alt="Hero" width="1200" height="630">
    <img data-src="/i/lazy.png"></body></html>"""
    links = extract_links(html, "https://x.test/")
    urls = [l["url"] for l in links]
    # nav link /a is site chrome (filtered); content link /p is kept
    assert "https://x.test/p" in urls and "https://x.test/a" not in urls
    assert not any(u.startswith("mailto:") for u in urls)
    assert next(l for l in links if l["url"].endswith("/p"))["text"] == "Precios"
    imgs = extract_images_info(html, "https://x.test/")
    assert imgs[0] == {"url": "https://x.test/i/hero.jpg", "alt": "Hero",
                       "width": 1200, "height": 630}
    assert imgs[1]["url"] == "https://x.test/i/lazy.png"
    assert extract_links("", "https://x.test/") == []


def test_main_content():
    html = """<html><body><nav>menu</nav>
    <aside class="sidebar">sidebar con mucho texto de relleno para superar el umbral mínimo de longitud requerido aquí mismo</aside>
    <article><h1>Guía real</h1><p>""" + ("Contenido principal valioso. " * 30) + """</p></article>
    <div class="comments">comentarios ruidosos con bastante texto también para probar que se eliminan del resultado final obtenido</div>
    </body></html>"""
    main = extract_main_text(html)
    assert main and "Guía real" in main
    assert "sidebar" not in main and "comentarios" not in main
    assert extract_main_text("<html><body><p>corto</p></body></html>") is None


def test_structured_product_jsonld():
    html = """<html><head><script type="application/ld+json">{
    "@context": "https://schema.org", "@type": "Product",
    "name": "Taza viaje", "brand": {"@type": "Brand", "name": "Acme"},
    "sku": "MUG-16", "offers": [
      {"@type": "Offer", "price": "24.99", "priceCurrency": "USD",
       "availability": "https://schema.org/InStock", "url": "https://x.test/v1"},
      {"@type": "Offer", "price": "29.99", "priceCurrency": "USD",
       "availability": "https://schema.org/OutOfStock"}]}
    </script></head><body><h1>Taza</h1></body></html>"""
    s = extract_structured(html)
    assert s and s["entityType"] == "product" and s["source"] == "jsonld"
    assert s["brand"] == "Acme" and s["sku"] == "MUG-16"
    assert s["availability"] == "in_stock"
    assert s["price"]["value"] == "24.99" and s["price"]["currency"] == "USD"
    assert len(s["variants"]) == 2
    assert reconcile_page_type("listing", [{"x": 1}], s) == ("product", [])


def test_structured_product_prefers_over_org():
    html = """<html><head><script type="application/ld+json">[
    {"@context": "https://schema.org", "@type": "Organization", "name": "Acme"},
    {"@context": "https://schema.org", "@type": "Product", "name": "Taza",
     "offers": {"@type": "Offer", "price": "9.99", "priceCurrency": "EUR"}}]
    </script></head><body></body></html>"""
    s = extract_structured(html)
    assert s and s["entityType"] == "product" and s["name"] == "Taza"


def test_cache_ttl():
    old = Record(url="https://x.test/", scrapedAt=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())
    fresh = Record(url="https://x.test/", scrapedAt=datetime.now(timezone.utc).isoformat())
    assert _cache_fresh(old, 0) is True  # TTL off = forever
    assert _cache_fresh(old, 86400) is False
    assert _cache_fresh(fresh, 86400) is True
    assert _parse_max_age("24h") == 86400.0
    assert _parse_max_age("7d") == 604800.0
    assert _parse_max_age("30m") == 1800.0
    assert _parse_max_age("3600") == 3600.0
    assert _parse_max_age(None) == 0.0
    assert _parse_wait_ms("2s") == 2000
