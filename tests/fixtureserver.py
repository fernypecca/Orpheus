"""Deterministic local fixture server for offline tests.

Every "scenario" from the brief maps to a route here, so tests are repeatable
and don't depend on internet access:
  /            simple page (no protection)
  /cookie      OneTrust-style consent banner
  /faq         collapsed content + XHR-backed FAQ (P0)
  /listing     repeated cards (P1 listing extractor)
  /profile     single entity (P1 profile extractor)
  /paginated   page that calls a paginated internal API (P1 replay)
  /api/faq     JSON endpoint
  /api/list?page=N  paginated JSON endpoint
  /robots.txt  Disallow: /private
  /private     robots-blocked page
  /blocked     403 + Cloudflare-style challenge
  /loop /loop2 /loop3   link graph for crawl-mode tests
  /meta-rich    meta/language-rich page (Fase 3)
  /gated        consent wall that gates content until rejected (Fase 4)
  /frames        page with cross-origin + same-origin iframes (Fase 4)
  /local-frame   same-origin iframe content (Fase 4)
  /flaky403      403 that clears after N requests (anti-bot retry, Fase 4)
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class State:
    def __init__(self):
        self.hits: Counter = Counter()


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>{body}</body></html>"""


def build_pages() -> dict:
    return {
        "/frames": """<!doctype html><html><head><meta charset="utf-8"><title>Frames</title></head>
<body><h1>Página con iframes</h1>
<iframe src="/local-frame" title="Local"></iframe>
<iframe src="https://cross-frame.test/content" title="Cross"></iframe>
<p>Texto de la página principal con iframes.</p>
</body></html>""",
        "/local-frame": """<!doctype html><html><head><meta charset="utf-8"><title>Frame local</title></head>
<body><p>Texto del iframe local.</p></body></html>""",
        "/": page(
            "Acme Simple Page",
            """
            <nav><a href="/">Inicio</a><a href="/cookie">Cookies</a><a href="/private">Privado</a></nav>
            <h1>Acme Simple Page</h1>
            <p>Este es el contenido principal de la página simple de Acme.</p>
            <footer>© 2026 Footer Content Que No Debe Aparecer</footer>
            <script>window.__secret = "este texto no debe salir";</script>
            """,
        ),
        "/cookie": page(
            "Acme Página",
            """
            <div id="onetrust-consent-sdk">
              <div class="onetrust-banner">
                <p>Utilizamos cookies y tecnologías similares para fines de marketing, propósitos estadísticos y proveedores IAB participantes.</p>
                <button id="onetrust-reject-all-handler">Rechazar todo</button>
                <button id="onetrust-accept-btn-handler" onclick="window.location='/consent-accepted'">Aceptar todo</button>
              </div>
            </div>
            <h1>Página de Acme</h1>
            <p>El contenido real que interesa al scraper.</p>
            """,
        ),
        "/guard": page(
            "Guard test",
            """
            <h1>Guard test</h1>
            <details><summary>Real details</summary><p>Contenido real visible.</p></details>
            <div class="cookie-notice"><p>Usamos cookies.</p><button aria-expanded="false" aria-controls="g1" onclick="window.location='/cookie-expanded'">Expandir dentro de cookie</button><div id="g1"><p>contenido oculto del contenedor</p></div></div>
            <button onclick="window.location='/purchased'" style="margin:4px">Comprar ahora</button>
            """,
        ),
        "/faq": page(
            "Acme FAQ",
            """
            <h1>Preguntas frecuentes</h1>
            <details><summary>¿Cómo funciona el envío?</summary><p>Enviamos en 24 horas a toda España.</p></details>
            <details><summary>¿Devoluciones?</summary><p>Tienes 30 días para devolver.</p></details>
            <div class="accordion"><button aria-expanded="false" aria-controls="acc1">Ver requisitos</button><div id="acc1" class="panel" style="display:none"><p>Requisitos: DNI y tarjeta.</p></div></div>
            <button id="load-faq" class="btn">Load more FAQs</button>
            <div id="faq-extra"></div>
            <button onclick="window.location='/purchased'" style="margin:4px">Comprar ahora</button>
            <div id="onetrust-consent-sdk"><button aria-expanded="false" onclick="window.location='/cookie-expanded'">Expandir dentro de cookie</button></div>
            <script>
            document.getElementById('load-faq').addEventListener('click', async function () {
              var r = await fetch('/api/faq');
              var data = await r.json();
              var box = document.getElementById('faq-extra');
              data.forEach(function (item) {
                var p = document.createElement('p');
                p.textContent = item.q + ' - ' + item.a;
                box.appendChild(p);
              });
            });
            </script>
            """,
        ),
        "/listing": page(
            "Acme Listado",
            "".join(
                f'<article class="card"><h2><a href="/profile?i={i}">Proveedor {i}</a></h2>'
                f'<p>Snippet del proveedor número {i} en Madrid.</p></article>'
                for i in range(6)
            )
        ),
        "/profile": page(
            "Proveedor 3",
            """
            <h1>Proveedor 3</h1>
            <meta name="description" content="Fotógrafo de bodas en Madrid con 10 años de experiencia.">
            <dl><dt>País</dt><dd>España</dd><dt>Año</dt><dd>2024</dd></dl>
            <address itemprop="address">Calle Mayor 3, Madrid</address>
            <p>Contacto: hola@proveedor3.com · +34 600 123 456</p>
            """,
        ),
        "/paginated": page(
            "Listado paginado",
            """
            <h1>Listado paginado</h1>
            <ul id="items"></ul>
            <script>
            fetch('/api/list?page=1').then(function (r) { return r.json(); }).then(function (data) {
              var ul = document.getElementById('items');
              data.items.forEach(function (it) {
                var li = document.createElement('li');
                li.textContent = it.name;
                ul.appendChild(li);
              });
            });
            </script>
            """,
        ),
        "/loop": page(
            "Loop hub",
            """
            <h1>Loop hub</h1>
            <nav><a href="/loop">self</a><a href="/loop2">two</a><a href="/loop3">three</a><a href="/private">privado</a><a href="http://example.test/outside">externo</a></nav>
            """,
        ),
        "/loop2": page("Loop two", "<h1>Loop two</h1><p>Contenido de la página dos.</p>"),
        "/loop3": page("Loop three", "<h1>Loop three</h1><p>Contenido de la página tres.</p>"),
        "/looputm": page(
            "Loop utm hub",
            """
            <h1>Loop utm hub</h1>
            <nav>
            <a href="/loop2?utm_source=email&utm_medium=newsletter">two utm</a>
            <a href="/loop2?fbclid=abc123">two fbclid</a>
            <a href="/loop3">three</a>
            </nav>
            """,
        ),
        "/private": page("Pagina privada", "<h1>Pagina privada</h1><p>Este contenido es privado.</p>"),
        "/withimg": page(
            "Con imagen",
            '<h1>Con imagen</h1><img src="/img.png" alt="test"><img data-src="/img2.png" alt="lazy">',
        ),
        "/structured-jsonld": page(
            "Fotografía Luna",
            """
            <h1>Fotografía Luna</h1>
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "LocalBusiness",
              "name": "Fotografía Luna",
              "description": "Fotógrafo de bodas en Madrid con 12 años de experiencia.",
              "priceRange": "€€",
              "telephone": "+34 610 000 111",
              "email": "hola@luna.example.com",
              "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.9", "bestRating": "5", "reviewCount": "127"},
              "review": [
                {"@type": "Review", "author": {"@type": "Person", "name": "María"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Un equipo maravilloso."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Juan"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Fotos espectaculares."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Ana"}, "reviewRating": {"@type": "Rating", "ratingValue": "4"}, "reviewBody": "Muy profesionales."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Luis"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Repetiremos sin duda."},
                {"@type": "Review", "author": {"@type": "Person", "name": "Sara"}, "reviewRating": {"@type": "Rating", "ratingValue": "5"}, "reviewBody": "Recomendadísimos."}
              ]
            }
            </script>
            <p>Contenido visible de la página.</p>
            """,
        ),
        "/structured-microdata": page(
            "Floristería Primavera",
            """
            <h1>Floristería Primavera</h1>
            <div itemscope itemtype="https://schema.org/LocalBusiness">
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
            </div>
            """,
        ),
        "/structured-meta": page(
            "Banquete Azahar",
            """
            <meta property="og:title" content="Banquete Azahar">
            <meta property="og:description" content="Banquetes de boda en Sevilla.">
            <meta property="og:image" content="https://cdn.example.com/azahar.jpg">
            <meta name="description" content="Banquetes de boda en Sevilla.">
            <meta name="price" content="€80–€120">
            <meta name="currency" content="EUR">
            <h1>Banquete Azahar</h1>
            """,
        ),
        "/structured-heuristic": page(
            "Música en Directo",
            """
            <h1>Música en Directo</h1>
            <div class="price">€600–€900</div>
            <div class="rating" data-rating="4.8">4.8</div>
            <span class="review-count">34</span>
            <nav class="breadcrumb"><a href="/">Inicio</a> <a href="/musica">Música</a> <a href="/dj">DJ</a></nav>
            <p>Contacto: dj@ejemplo.com</p>
            """,
        ),
        "/structured-none": page("Página plana", "<h1>Página plana</h1><p>Sin datos estructurados.</p>"),
        "/meta-rich": """<!doctype html><html lang="es"><head>
<meta charset="utf-8">
<title>Fotografía Alba</title>
<meta property="og:title" content="Fotografía Alba">
<meta property="og:description" content="Fotógrafa de bodas en Valencia.">
<meta property="og:image" content="/img/alba.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="/fotografia-alba">
<meta name="author" content="Alba Ruiz">
<meta property="article:published_time" content="2026-01-15T10:00:00Z">
<link rel="icon" href="/favicon.png">
</head><body><h1>Fotografía Alba</h1><p>Fotografías de bodas con estilo documental.</p></body></html>""",
        "/gated": """<!doctype html><html><head><meta charset="utf-8"><title>Pagina con consent</title></head>
<body>
<div id="onetrust-consent-sdk">
  <div class="onetrust-banner">
    <button id="onetrust-reject-all-handler">Reject</button>
    <button id="onetrust-accept-btn-handler">Accept</button>
  </div>
</div>
<h1>Pagina con consent</h1>
<p>Contenido inicial visible de la pagina con consentimiento.</p>
<div id="gated-content"></div>
<script>
document.getElementById('onetrust-reject-all-handler').addEventListener('click', function () {
  document.getElementById('onetrust-consent-sdk').style.display = 'none';
  setTimeout(function () {
    var el = document.createElement('p');
    el.id = 'late-content';
    el.textContent = 'Contenido adicional que carga tras rechazar el consent y aparece en el DOM mas tarde.';
    document.getElementById('gated-content').appendChild(el);
  }, 800);
});
</script>
</body></html>""",

    }


class Handler(BaseHTTPRequestHandler):
    state: State = State()
    pages: dict = build_pages()

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        self.state.hits[path] += 1

        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /private\nCrawl-delay: 0\n")
            return
        if path == "/api/faq":
            body = json.dumps([
                {"q": "¿Cuánto cuesta el plan pro?", "a": "49 euros al mes con soporte 24/7."},
                {"q": "¿Hay prueba gratuita?", "a": "Sí, 14 días sin tarjeta."},
            ]).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/list":
            page_no = int(urlparse(self.path).query.split("=")[1]) if "=" in urlparse(self.path).query else 1
            if page_no >= 3:
                items = []
            else:
                items = [{"name": f"Item pag {page_no} - {i}"} for i in range(2)]
            self._send(200, json.dumps({"items": items}).encode(), "application/json; charset=utf-8")
            return
        if path == "/blocked":
            body = (
                '<html><head><title>Attention Required! | Cloudflare</title></head>'
                '<body><div id="challenge-form">Comprobando tu navegador...</div></body></html>'
            ).encode()
            self._send(403, body, headers={"Server": "cloudflare", "cf-ray": "fixture-ray"})
            return
        if path == "/flaky":
            if self.state.hits["/flaky"] <= 1:
                self._send(500, b"boom")
                return
            html = self.pages.get("/") or b""
            if isinstance(html, str):
                html = html.encode("utf-8")
            self._send(200, html)
            return
        if path == "/flaky403":
            q = urlparse(self.path).query
            fails = int(q.split("fails=")[1]) if "fails=" in q else 999
            if self.state.hits["/flaky403"] <= fails:
                self._send(403, b"blocked", headers={"Server": "cloudflare", "cf-ray": "flaky"})
                return
            html = self.pages.get("/") or b""
            if isinstance(html, str):
                html = html.encode("utf-8")
            self._send(200, html)
            return
        if path == "/img.png" or path == "/img2.png":
            # 1x1 transparent PNG
            png = bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000d49444154789c6360010000050001d78592b60000000049454e44ae426082"
            )
            self._send(200, png, "image/png")
            return
        if path == "/sitemap.xml":
            base = f"http://{self.headers['Host']}"
            body = f"""<?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>{base}/</loc></url>
              <url><loc>{base}/loop2</loc></url>
              <url><loc>{base}/private</loc></url>
            </urlset>""".encode()
            self._send(200, body, "application/xml; charset=utf-8")
            return

        html = self.pages.get(path)
        if html is None:
            self._send(404, b"not found")
            return
        self._send(200, html.encode("utf-8"))


def make_server(state: State) -> ThreadingHTTPServer:
    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return server


def run_server(server: ThreadingHTTPServer) -> None:
    server.serve_forever(poll_interval=0.05)


class FixtureServer:
    def __init__(self):
        self.state = State()
        self.server = make_server(self.state)
        self.thread = threading.Thread(target=run_server, args=(self.server,), daemon=True)

    @property
    def base(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return self.base + path

    def __enter__(self) -> "FixtureServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()