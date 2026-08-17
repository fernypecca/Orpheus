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