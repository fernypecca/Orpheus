"""Unit tests for the structured extractor (pure HTML parsing, no browser)."""

from conftest import base_cfg, run, scrape_url

from growth_scraper.structured import extract_structured

JSONLD_HTML = '''<!doctype html><html><head><title>Fotografía Luna</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "Fotografía Luna",
  "description": "Fotógrafo de bodas en Madrid con 12 años de experiencia.",
  "image": "https://cdn.example.com/luna.jpg",
  "url": "https://luna.example.com",
  "telephone": "+34 610 000 111",
  "email": "hola@luna.example.com",
  "priceRange": "€€",
  "address": {"@type": "PostalAddress", "streetAddress": "Calle Luna 7", "addressLocality": "Madrid", "postalCode": "28004", "addressCountry": "ES"},
  "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.9", "bestRating": "5", "reviewCount": "127"},
  "review": [
    {"@type": "Review", "author": {"@type": "Person", "name": "María"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Un equipo maravilloso."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Juan"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Fotos espectaculares."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Ana"}, "reviewRating": {"@type": "Rating", "ratingValue": "4"}, "reviewBody": "Muy profesionales."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Luis"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Repetiremos sin duda."},
    {"@type": "Review", "author": {"@type": "Person", "name": "Sara"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Recomendadísimos."}
  ]
}
</script></head><body><h1>Fotografía Luna</h1></body></html>'''

MICRODATA_HTML = '''<div itemscope itemtype="https://schema.org/LocalBusiness">
  <meta itemprop="name" content="Floristería Primavera">
  <meta itemprop="description" content="Flores para bodas en Valencia.">
  <meta itemprop="priceRange" content="€€€">
  <div itemprop="aggregateRating" itemscope itemtype="https://schema.org/AggregateRating">
    <meta itemprop="ratingValue" content="4.7">
    <meta itemprop="bestRating" content="5">
    <meta itemprop="reviewCount" content="88">
  </div>
  <span itemprop="telephone">+34 963 000 222</span>
  <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
    <meta itemprop="streetAddress" content="Calle Flor 2">
    <meta itemprop="addressLocality" content="Valencia">
  </div>
</div>'''

META_HTML = '''<meta property="og:title" content="Banquete Azahar">
<meta property="og:description" content="Banquetes de boda en Sevilla.">
<meta property="og:image" content="https://cdn.example.com/azahar.jpg">
<meta name="description" content="Banquetes de boda en Sevilla.">
<meta name="price" content="€80–€120">
<meta name="currency" content="EUR">
<h1>Banquete Azahar</h1>'''

HEURISTIC_HTML = '''<h1>Música en Directo</h1>
<div class="price">€600–€900</div>
<div class="rating" data-rating="4.8">4.8</div>
<span class="review-count">34</span>
<nav class="breadcrumb"><a href="/">Inicio</a> <a href="/musica">Música</a> <a href="/dj">DJ</a></nav>
<p>Contacto: dj@ejemplo.com</p>'''


def test_jsonld_priority_and_fields():
    s = extract_structured(JSONLD_HTML)
    assert s["source"] == "jsonld"
    assert s["entityType"] == "profile"
    assert s["name"] == "Fotografía Luna"
    assert s["rating"] == {"value": 4.9, "best": 5.0, "count": 127}
    assert s["price"] == {"value": "€€", "currency": None, "isRange": True}
    assert len(s["reviews"]) == 3  # capped at 3
    assert s["reviews"][0]["author"] == "María"
    assert s["reviews"][0]["rating"] == 5.0
    assert s["contact"]["phone"] == "+34 610 000 111"
    assert s["contact"]["email"] == "hola@luna.example.com"
    assert s["contact"]["address"]["street"] == "Calle Luna 7"
    assert s["contact"]["website"] == "https://luna.example.com"


def test_microdata():
    s = extract_structured(MICRODATA_HTML)
    assert s["source"] == "microdata"
    assert s["entityType"] == "profile"
    assert s["name"] == "Floristería Primavera"
    assert s["price"] == {"value": "€€€", "currency": None, "isRange": True}
    assert s["rating"] == {"value": 4.7, "best": 5.0, "count": 88}
    assert s["contact"]["phone"] == "+34 963 000 222"
    assert s["contact"]["address"]["locality"] == "Valencia"


def test_meta():
    s = extract_structured(META_HTML)
    assert s["source"] == "meta"
    assert s["name"] == "Banquete Azahar"
    assert s["description"] == "Banquetes de boda en Sevilla."
    assert s["price"] == {"value": "€80–€120", "currency": "EUR", "isRange": True}


def test_heuristic_fallback():
    s = extract_structured(HEURISTIC_HTML)
    assert s["source"] == "heuristic"
    assert s["price"] == {"value": "€600–€900", "currency": None, "isRange": True}
    assert s["rating"]["value"] == 4.8
    assert s["rating"]["count"] == 34
    assert s["contact"]["email"] == "dj@ejemplo.com"
    assert s["category"]


def test_empty_html_none():
    assert extract_structured("") is None


def test_no_signals_none():
    assert extract_structured("<html><body><p>hola</p></body></html>") is None


def test_broken_json_no_raise():
    assert extract_structured("<script type='application/ld+json'>{not json}</script>") is None


def test_listing_entity_itemcount():
    html = ('<script type="application/ld+json">'
            '{"@type":"ItemList","name":"Proveedores",'
            '"itemListElement":[{"@type":"ListItem","position":1},{"@type":"ListItem","position":2}]}'
            '</script>')
    s = extract_structured(html)
    assert s["source"] == "jsonld"
    assert s["entityType"] == "listing"
    assert s["itemCount"] == 2


def test_jsonld_graph_flattening():
    html = ('<script type="application/ld+json">'
            '{"@context":"https://schema.org","@graph":['
            '{"@type":"LocalBusiness","name":"Estudio Alba","aggregateRating":{"@type":"AggregateRating","ratingValue":"4.6","reviewCount":"54"}},'
            '{"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Inicio"},{"@type":"ListItem","position":2,"name":"Fotógrafos"}]}'
            ']}</script>')
    s = extract_structured(html)
    assert s["source"] == "jsonld"
    assert s["entityType"] == "profile"
    assert s["name"] == "Estudio Alba"
    assert s["rating"]["value"] == 4.6
    assert s["category"] == "Inicio > Fotógrafos"


def test_e2e_jsonld_through_browser(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-jsonld")))
    s = rec.structured
    assert s["source"] == "jsonld"
    assert s["entityType"] == "profile"
    assert s["name"] == "Fotografía Luna"
    assert s["rating"]["count"] == 127
    assert len(s["reviews"]) == 3
    assert rec.summary["structuredRatingValue"] == 4.9
    assert rec.summary["structuredReviewCount"] == 127
    assert rec.summary["structuredSource"] == "jsonld"


def test_e2e_microdata_through_browser(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-microdata")))
    s = rec.structured
    assert s["source"] == "microdata"
    assert s["price"]["value"] == "€€€"
    assert s["contact"]["address"]["locality"] == "Valencia"


def test_e2e_heuristic_through_browser(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-heuristic")))
    s = rec.structured
    assert s["source"] == "heuristic"
    assert s["price"]["value"] == "€600–€900"
    assert rec.summary["structuredPrice"] == "€600–€900"


def test_e2e_no_signals_structured_none(fs):
    rec = run(scrape_url(base_cfg(), fs.url("/structured-none")))
    assert rec.structured is None
    assert "structuredSource" not in rec.summary


def test_csv_structured_columns(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    code = main([fs.url("/structured-jsonld"), "-o", str(out), "--csv",
                 "--delay", "0", "--jitter", "0"])
    assert code == 0
    csv_path = tmp_path / "cli.csv"
    header = csv_path.read_text().splitlines()[0]
    assert "structuredSource" in header
    assert "structuredRatingValue" in header
    row = csv_path.read_text().splitlines()[1]
    assert "Fotografía Luna" in row
    assert "jsonld" in row