"""Live-test fixes: nav-chrome filter, HubSpot consent, main-content guard,
SoftwareApplication as product. No browser needed (stubs + pure functions)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from growth_scraper import consent as consent_mod
from growth_scraper.extractors import run_extraction
from growth_scraper.maincontent import extract_main_text
from growth_scraper.pagemeta import extract_links
from growth_scraper.structured import extract_structured

NAV_HTML = """<html><body>
<header class="SiteHeader"><nav><ul><li><a href="/p1">Products</a></li></ul></nav>
<div class="MobileMenu"><ul><li class="mnav"><a href="/m1">Mobile link one</a></li>
<li class="mnav"><a href="/m2">Mobile link two</a></li>
<li class="mnav"><a href="/m3">Mobile link three</a></li>
<li class="mnav"><a href="/m4">Mobile link four</a></li>
<li class="mnav"><a href="/m5">Mobile link five</a></li></ul></div></header>
<h1>Real pricing page</h1><p>Plans start at 10 dollars per user per month.</p>
<p><a href="/signup">Start free trial today</a></p>
<footer><ul><li><a href="/f1">Footer link</a></li></ul></footer>
</body></html>"""


def _result(html):
    return SimpleNamespace(cleaned_html="", html=html, url="https://x.test/pricing")


def test_nav_menus_not_listing():
    ptype, items = run_extraction(_result(NAV_HTML))
    assert ptype != "listing", (ptype, items)
    assert items == []


def test_links_skip_navigation():
    links = extract_links(NAV_HTML, "https://x.test/")
    urls = [l["url"] for l in links]
    assert urls == ["https://x.test/signup"], urls


def test_hubspot_consent_selectors():
    assert "#hs-eu-decline-button" in consent_mod._CMP_REJECT_SELECTORS
    strong = ",".join(consent_mod._STRONG_CONTAINER_SELECTORS)
    assert "hs-eu-cookie" in strong and "hs-cookie" in strong
    assert "hs-eu-cookie-confirmation" not in ""  # sanity, real check below


def test_hubspot_banner_matches_js_queries():
    """The live HubSpot banner must match the consent JS container+reject path."""
    from bs4 import BeautifulSoup

    html = """<div id="hs-eu-cookie-confirmation"><div id="hs-eu-cookie-confirmation-inner">
    <div id="hs-eu-policy-wording"><p>We use cookies to improve.</p></div>
    <button id="hs-eu-decline-button">Decline all</button>
    <button id="hs-eu-confirmation-button">Accept</button></div></div>"""
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one(",".join(consent_mod._STRONG_CONTAINER_SELECTORS)) is not None
    assert soup.select_one("#hs-eu-decline-button") is not None


class _FakePage:
    def __init__(self):
        self.wait_calls = 0

    async def evaluate(self, js):
        return "no-consent-found"

    async def wait_for_selector(self, *a, **k):
        self.wait_calls += 1
        raise TimeoutError("none")


def test_late_recheck_has_no_wait_tax():
    page = _FakePage()
    out = asyncio.run(consent_mod.handle_consent(page, iterations=1, late_wait=False))
    assert out == "no-consent-found"
    assert page.wait_calls == 0


def test_main_content_guard_on_listing():
    # Cards as separate top-level divs with no common wrapper (stripe.com/blog
    # shape): no single card holds a fair share -> keep full text (None).
    cards = "".join(
        f"<div class='card'><h2>Post {i}</h2><p>{'Resumen breve del post. ' * 12}</p>"
        f"<a href='/a{i}'>Leer mas</a></div>"
        for i in range(8)
    )
    html = (f"<html><body><div class='site-header'>{'cabecera ' * 40}</div>"
            f"{cards}<div class='site-footer'>{'pie ' * 40}</div></body></html>")
    assert extract_main_text(html) is None  # no single card IS the page


def test_main_content_keeps_article():
    body = "Contenido principal valioso. " * 60
    html = (f"<html><body><nav>menu</nav><aside class='sidebar'>relleno lateral "
            f"{'x ' * 100}</aside><article><h1>Guía</h1><p>{body}</p></article></body></html>")
    main = extract_main_text(html)
    assert main and "Guía" in main and "relleno lateral" not in main


def test_software_application_is_product():
    html = """<html><head><script type="application/ld+json">{
    "@context": "https://schema.org", "@type": "SoftwareApplication",
    "name": "Jira", "applicationCategory": "BusinessApplication",
    "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
    </script></head><body><h1>Jira</h1></body></html>"""
    s = extract_structured(html)
    assert s and s["entityType"] == "product" and s["price"]["value"] == "0"


def test_items_dedupe_exact_duplicates():
    """Plan matrices repeat the same feature row per tier (slack.com/pricing):
    exact (title, href) duplicates collapse, distinct-href variants stay."""
    from growth_scraper.extractors import extract_items
    from bs4 import BeautifulSoup

    html = """<html><body><ul>
    <li><a href="/ai">Basic AI</a></li><li><a href="/ai">Basic AI</a></li>
    <li><a href="/s1">Search</a></li><li><a href="/s2">Search</a></li>
    </ul></body></html>"""
    items = extract_items(BeautifulSoup(html, "html.parser"), "https://x.test/")
    assert [(i["title"], i["href"]) for i in items] == [
        ("Basic AI", "https://x.test/ai"),
        ("Search", "https://x.test/s1"),
        ("Search", "https://x.test/s2"),
    ]
