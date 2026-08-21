# AGENTS.md — growth-scraper (gscrape)

> Para la próxima IA/sesión que toque este repo. Léelo antes de cambiar nada.

## Qué es

Scraper genérico y pulido para research de growth marketing, basado en
Crawl4AI 0.9.x. CLI `gscrape`, output JSONL listo para LLMs. Cero plataformas
de pago, corre en local.

## Arquitectura (`src/growth_scraper/`)

| Módulo | Responsabilidad |
|---|---|
| `cli.py` | argparse + loop principal. **Un único `asyncio.run`** para start→crawl→close (ver decisión D7). |
| `config.py` | Constantes y defaults (delays, caps, blacklists multi-idioma, selectores). Todo lo tunable vive acá. |
| `robots.py` | `urllib.robotparser` + cache disco; respeta Allow/Disallow/Crawl-delay. Fail-open si no se puede fetchear (log warning). |
| `pacing.py` | delay + jitter entre páginas. |
| `netrec.py` | **P0**. Listener Playwright (request + response + bodies JSON). Filtra: same-origin, JSON, no-analytics, no-triviales, cap 500 KB. |
| `consent.py` | **Reject-only** (nunca acepta). Selectores CMP + texto multi-idioma; abre settings si solo hay accept; remueve contenedores del DOM. |
| `clickguard.py` | Blacklist de clicks (buy/checkout/delete/login/aceptar…) + exclusión de contenedores cookie/consent. Comparte JS con el browser. |
| `expand.py` | **P0**. Expande por semántica (`<details>`→`.open=true`, `aria-expanded=false`, accordion, tabs, load-more) + scroll lazy-load. Guardado por blacklist + contenedores. |
| `apipage.py` | **P1**. Replay de endpoints internos paginados (page/offset/limit) dentro del browser (mismo origen/cookies), cap de páginas. |
| `extractors.py` | **P1**. Clasifica `listing` vs `profile` vs `generic` y extrae `items`. |
| `protection.py` | Fail-closed: detecta Cloudflare/DataDome/Akamai y falla explícito. |
| `pipeline.py` | Orquesta 1 URL: robots → cache → crawl4ai (hooks) → protección → texto → extractores → apiResponses → imágenes → cache. |
| `crawl.py` | BFS mismo-dominio con tope. Descubrimiento de links desde el HTML crudo. **Workers concurrentes** (`--concurrency`, robots + pacing por dominio respetados). |
| `urlutil.py` | Normalización de URLs: quita `utm_*`/fbclid/gclid/params de sesión → dedupe y cache limpios. |
| `sitemap.py` | Seed desde sitemap (`--sitemap`): index/urlset, namespace-aware, filtrado por robots, cap. |
| `server.py` | FastAPI + uvicorn: `gscrape serve`, single warm `AsyncWebCrawler`, `POST /scrape`, cache fast-path, `--token`. Binds 127.0.0.1. |
| `structured.py` | P2: entidades estructuradas (JSON-LD → microdata → meta/OG → heurística), fail-open, campo `structured` + triage en `summary`/CSV. |
| `meta.py` | Idioma (ISO 639-1, heurística sin deps, fail-open) + metadata rica (`meta`: canonical, OG, twitter, author, publishedAt, favicon) |
| `screenshot.py` | Screenshot full-page CLI-only (`--screenshots DIR`), campo `screenshots` con rutas |
| `waitcontent.py` | Espera acotada (poll de `innerText`, cap `consent_wait_ms`) tras dismiss de consent → contenido gated de SPAs capturado completo. Fail-open. |
| `iframes.py` | Reporta iframes (`frames`) + fetch polite del `src` (`frameTexts`): robots-respecting, concurrency 3, timeout 5s, truncado 2000, fail-open por frame |
| `utils.py` | `build_headers` — headers polite (UA honesto + `X-Crawl4AI-Untouched`) para fetches extra |

`tests/` corre contra un **fixture server local** (`fixtureserver.py`), no
necesita internet. `scripts/` tiene los 5 escenarios + `fixture-check.sh`.

## Decisiones tomadas (y por qué)

- **D1 — No usar `remove_consent_popups` de Crawl4AI.** Clickea "Accept All"
  primero, viola el guardrail. Implementamos `consent.py` reject-only.
- **D2 — Ocultar con CSS no basta.** `page.content()` serializa nodos con
  `display:none`; el banner seguía en el markdown. Hay que **remover** los
  contenedores del DOM (`el.remove()`). Verificado empíricamente (D8).
- **D3 — Selector de remoción escalonado.** `cookie`/`consent` genéricos solo
  se tratan como contenedor si tienen UI (button/input/select), para no borrar
  contenido legítimo (ej. recetas con `class="cookie"`). Los marcadores CMP
  fuertes (onetrust, didomi, cookiebot, cmp…) se remueven siempre.
- **D4 — capture_network_requests de Crawl4AI no da bodies de forma fiable.**
  `netrec.py` usa su propio listener Playwright en el hook
  `on_page_context_created` para leer `resp.json()`.
- **D5 — Replay de paginación (P1) dentro del browser**, en el hook
  `before_retrieve_html`: la página sigue viva, mismo origen/cookies, y se ve
  igual que el uso normal del sitio. Antes del replay se **desactiva** el
  recorder para no re-capturar los fetches del propio replay (evita dupes).
- **D6 — `page.evaluate` + `asyncio.to_thread` para robots**: client httpx por
  fetch, no compartido entre threads.
- **D7 — Un solo event loop.** Bug real encontrado: cerrar el crawler con un
  segundo `asyncio.run` (en el `finally` de `main`) **cuelga** porque el
  browser está atado al loop original. Todo (start→crawl→close) corre en el
  mismo `asyncio.run`.
- **D8 — Descubrimiento de links desde HTML crudo**, no desde
  `result.links`: Crawl4AI lo deriva del DOM *limpio*, así que los links dentro
  de `<nav>`/`<footer>` (que excluimos) se perdían. `bs4` sobre `result.html`.
- **D9 — `excluded_tags=["nav","footer","form","aside","script","style"]`**.
  No excluimos `<header>` (riesgo de perder el h1). Trade-off documentado.
- **D10 — Honest UA** `GrowthScraperBot/0.1`. Identidad real + robots
  respetado. Sobreescribible si hace falta.
- **D11 — Texto limpio de scripts**: el markdown de Crawl4AI ya excluye
  scripts/styles; los tests lo verifican.
- **D12 — Cache propio de records** (`--cache-dir`) en vez del cache de
  Crawl4AI: más transparente, por URL, y sirve el record completo (incluye
  apiResponses). Robots.txt también se cachea en disco.
- **D13 — `remove_overlay_elements=False`.** Bug real encontrado en validación
  live: con `True`, crawl4ai dejaba Wikipedia en 1 char de markdown
  (cleaned_html de 232KB → 689 chars). El removedor de overlays de crawl4ai es
  demasiado agresivo. Nuestro consent handler ya cubre los overlays reales.
- **D14 — Detección de protección: solo señales de challenge real.** En
  validación live: `cf-ray`/CDN headers (Britannica, CloudFront/Cloudflare CDN)
  NO son bloqueo — la mayoría de sitios grandes usan CDN y sirven normal. Se
  bloquea solo con 403/429 + server anti-bot, títulos de challenge, o shells
  de challenge en HTML. También: marcadores de texto como "datadome" en HTML
  daban falsos positivos en artículos que *mencionan* anti-bot (Wikipedia).
  Ahora solo marcadores estructurales (`challenge-form`, `cf_chl_opt`,
  `cf-browser-verification`, `_px3`, `__cf_chl`).
- **D15 — Consent: tras un reject que funciona, NO tocar más el DOM.** Bug
  real en IONOS (consent wall Svelte): manipular el DOM tras dismiss rompía la
  hidratación de la app → página vacía. Ahora: reject exitoso → retorno
  temprano; solo si el reject fue no-op o no existe, se ocultan los árboles
  overlay (fixed/absolute, seguro para SPAs) y se remueven solo banners
  in-flow estáticos (garantiza texto limpio).
- **D16 — Descubrimiento de links con `<a href>` del HTML crudo** (ver D8).
- **D17 — Normalización de URLs para dedupe.** `utm_*`, fbclid, gclid, params de
  sesión se eliminan (`urlutil.py`) en entrada de CLI, seeds de crawl y enlaces
  descubiertos. Los records guardan la URL normalizada (limpia de tracking).
- **D18 — Sesión por-run para concurrencia.** El `Session` compartido no
  sobrevive a `arun` paralelos. Ahora cada `run_one` crea su `Session` y se la
  asocia por `config.session_id` (los hooks reciben el `config` y lo leen vía
  `getattr(config, "session_id", None)`). Tras `arun` se hace
  `kill_session(sid)` para no filtrar contexts de Chromium.
- **D19 — Retry con backoff.** Errores transitorios (timeout/exception de
  `arun`) y `status_code >= 500` se reintentan con backoff exponencial
  (`--max-retries`, default 2). Si el 5xx persiste → `HTTP_ERROR: NNN`.
- **D20 — `text` LLM-ready.** `--max-text-chars` (default 12000) capa el texto
  (truncado en límite de palabra) para que quepa en el TPM de tiers baratos
  (Groq/NVIDIA gratis). `--fit-text` (opt-in) usa `fit_markdown` de crawl4ai
  (PruningContentFilter) — opt-in porque el filtrado de crawl4ai ya rompió
  páginas enteras en D13. Preferimos raw + cap por defecto.
- **D21 — Sitemaps namespace-aware.** `root.iter("loc")` no matchea tags con
  namespace XML (`{...}loc`); se filtra por `tag.split("}")[-1]`. Validado live
  contra el sitemapindex real de BBC (3 páginas crawleadas con concurrencia 2).
- **D22 — Fail-closed para "stall" silencioso.** `demo.datadome.co` servía
  `status_code=None` + título/texto vacíos sin que ninguna regla lo cazara.
  Nueva regla 5 en `protection.py`: sin status HTTP y payload vacío →
  `PROTECTION_BLOCKED`. Verificado live.
- **D23 — Imágenes extraídas del HTML crudo, no de `result.media`.**
  Bug de crawl4ai 0.9.2: con `excluded_tags` seteado (lo necesitamos para el
  texto limpio) `result.media` devuelve **cero** imágenes. `_extract_image_urls`
  parsea `<img src/data-src/srcset>` del HTML crudo con bs4 (soporta lazy-load)
  y descarga con httpx. Verificado live en www.python.org.
- **D24 — Hotlink protection real.** Wikipedia (upload.wikimedia.org) responde
  403 a todo (con y sin Referer) — limitación inherente del host, documentada.
- **D25 — uvicorn es dueño del event loop.** El server no llama `asyncio.run` por
  request; el crawler se crea/cierra en el lifespan de la app (compatible con D7).
- **D26 — Sesión por request** (D18) en server mode. Cada `POST /scrape` crea su
  `Session` y hace `kill_session` al terminar, aunque falle.
- **D27 — 200 siempre que el scrape corra.** `protectionBlocked`/`error` viven en el
  record (igual que CLI). Errores de contrato/transporte → 400 (body inválido) o 500.
- **D28 — `structured` fail-open.** Cualquier error o HTML sin señales → `structured:
  null`. Nunca rompe el record. `source` = primera fuente con ≥1 campo (jsonld >
  microdata > meta > heuristic).
- **D29 — Server single-URL y solo loopback.** SSRF documentado; `--token` opcional.
  El cliente de referencia intenta el server primero y cae a spawn si no responde.
- **D30** — Idioma fail-open sin dependencias: `<html lang>` → `content-language` → stopwords (texto ≥80 palabras, ratio ≥0.05, margen 1.5×). Sin señal → `None`.
- **D31** — `meta` es un campo de shape estable (keys con `null`), separado del triage de `summary` (solo `language` ahí y en CSV).
- **D32** — Screenshots solo CLI (`--screenshots DIR`), PNG full-page en disco, campo puntero `screenshots`; el server nunca escribe a disco.
- **D33** — Captura en `before_retrieve_html` (página viva y expandida); `session.screenshot_path` → `record.screenshots` solo en éxito.
- **D34 — Contenido gated: espera acotada, nunca infinita.** Tras una acción de
  consent que SÍ hace algo (reject/hide/remove) `waitcontent.py` pollea
  `document.body.innerText.length` cada 500ms hasta estabilizar (Δ<5% en 2
  polls) o el cap `consent_wait_ms` (5000ms). Fail-open. No se espera si el
  consent fue no-op/error. Coste para Orpheus: 0 (la espera solo corre tras
  dismiss).
- **D35 — Iframes: reportar + fetch polite del `src`.** No ejecutamos script
  del tercero (sería ejecutar código ajeno); GET limpio con UA honesto +
  `X-Crawl4AI-Untouched`, robots-respecting (`is_allowed`), concurrency 3,
  timeout 5s, texto truncado a 2000, fail-open por frame con marcadores
  honestos (`Error: timeout`/`Error: fetch`/`Skipped by robots` — NUNCA datos
  fabricados). `crossOrigin` = netloc del src ≠ netloc de la página.
- **D36 — Anti-bot intermitente: retry acotado con evidencia.** `attempts = 1 +
  max_retries + anti_bot_retries`. 5xx siguen con backoff corto; 403/429 usan
  `anti_bot_backoff_s + jitter` (default 15s), cap duro `anti_bot_retries`
  (default 2). `record.retries` = reintentos reales. robots disallowed → hard
  stop sin retry. Persistencia → `PROTECTION_BLOCKED` (fail-closed). Sin
  evasión.
- **D37 — 403/429 sin servidor anti-bot también es bloque.** Refina D14: un CDN
  (cf-ray, server: cloudflare) sirviendo 200 NO es bloqueo, pero un *status*
  403/429 en el HTML principal (rate limit de nginx/Fastly, IP ban, geo block)
  siempre es un fallo → `PROTECTION_BLOCKED: HTTP NNN (blocked or rate limited)`.
  Antes el record salía con status 429, `error: None` y el texto de la página de
  rate-limit filtrándose a `text` (falla silenciosa).
- **D38 — Iframes: GET en streaming, redirects seguidos, solo http(s).** El
  fetch del `src` sigue redirects (`follow_redirects=True`, los embeds suelen
  redirigir), lee en streaming con cap de 64KB (no baja el body entero), y solo
  acepta esquemas http/https (nada de `mailto:`/`file:`/`data:`). El cap de
  `max_frames` se aplica DESPUÉS de filtrar los srcs descartables.
- **D39 — `pageType` cede ante `structured` cuando la fuente es confiable.**
  Bug real, verificado en vivo en bodas.net: `classify()` (heurística de
  `<li>`/`<article>`/`<dt>`/`<address>`) marcaba perfiles de proveedor como
  `"listing"` con `items` basura (links de nav, pies de foto), porque el nav
  del header y el carrusel de fotos igual llegan a 4+ `<li>`, y bodas.net no
  usa `<dt>`/`itemprop`/`<address>` para que la heurística los reconozca como
  perfil. `structured.py` sí lee el JSON-LD real (`LocalBusiness`) de esa misma
  página y clasifica bien. `reconcile_page_type()` en `extractors.py`: si
  `structured.source` es `jsonld`/`microdata` (nunca `meta`/`heuristic`, muy
  débiles para arbitrar) y su `entityType` no coincide con el `pageType` de la
  heurística, gana `structured` — y se descartan los `items` viejos en vez de
  dejarlos mostrando basura junto al tipo ya corregido.
- **D40 — SSRF guard en `/scrape`.** El server solo escucha en `127.0.0.1`
  (ver docstring de `server.py`), pero eso no alcanza: `/scrape` acepta
  cualquier URL http(s) de quien sea que le hable al server, y el día que algo
  lo exponga (un reverse proxy adelante, por ejemplo — el plan real para
  producción) ese "quien sea" deja de ser de confianza. `_is_ssrf_target()`
  resuelve el host y rechaza loopback/RFC1918/link-local/reservado/multicast
  antes de scrapear — cubre explícitamente el caso de endpoints de metadata de
  nube (169.254.169.254 y similares). Hueco conocido, no cerrado acá: no
  protege contra un redirect que lleve a una IP interna *después* de este
  chequeo — necesitaría engancharse a los eventos de navegación de Playwright,
  no solo a esta validación previa.
- **D41 — Workflow: PR a main, con dientes reales.** El intento anterior de
  esto (rama `chore/pr-workflow`, ya abandonada) instalaba un pre-push hook
  *local* — protegía un solo clone, cualquier otro (incluido este) quedaba sin
  nada. Al momento de esa PR el repo todavía era privado y sin CI, así que la
  nota decía "activar esto cuando sea público" — pero ya es público (ver
  release pública, commit `aeede34`) y con eso la protección de rama real de
  GitHub ya está disponible, no es algo "para más adelante". Reemplazado por:
  `.github/workflows/test.yml` corre el suite completo en cada PR/push a
  `main`, y un ruleset de GitHub exige ese check en verde antes de mergear —
  aplica a cualquier clone, no a uno.
- **D42 — `capture_network_requests=False`.** Estaba en `True`: el capturador
  propio de crawl4ai, redundante con `netrec.py` (ver D4 — netrec.py existe
  *porque* el de crawl4ai "no da bodies de forma fiable") y sin ningún
  consumidor en el código (verificado: cero referencias fuera de este flag).
  Tenerlo prendido no era solo trabajo de más: pisaba un bug real de
  `crawl4ai` (`async_crawler_strategy.py`) — cuando `response.text()` falla
  (body vacío/binario, ej. beacons de tracking), la rama `except` deja
  `text_body` sin asignar (`# text_body = None`, comentado) pero el dict de
  abajo lo referencia igual → `UnboundLocalError`, atrapado un nivel arriba y
  logueado como warning `[CAPTURE]`. Salía en cada corrida contra bodas.net
  (su endpoint de tracking). Verificado en vivo: `pageType`/`structured`/
  `apiResponses` sin cambios tras apagarlo (netrec.py sigue capturando igual).
- **D43 — `max_concurrency` del server: 4 → 6, con benchmark real.** 10 perfiles
  reales de bodas.net (mismo dominio — el escenario de riesgo real para
  anti-bot), vía `gscrape serve` local: concurrencia 2 → 31.1s, 6 → 13.6s,
  10 → 10.0s. **Cero errores/`protectionBlocked` en ningún nivel**, ni al
  paralelizar al máximo contra el mismo sitio — la preocupación de que más
  concurrencia dispare más detección anti-bot no se vio en esta prueba. Se
  achican los retornos pasado 6 (+4 concurrencia de 2→6 ahorra 17.5s; de 6→10
  solo 3.6s), así que ahí quedó el default nuevo. **Límites de esta prueba,
  a tener en cuenta**: 10 URLs de un solo dominio, una sola corrida, en la
  Mac de Fer (más núcleos que el VPS de 2 OCPU donde esto va a correr en
  producción — Chromium renderiza con CPU real, vale la pena remedir una vez
  que el VPS esté arriba). `--concurrency` de `--crawl` (BFS, código
  separado del server) **no se tocó** — mismo riesgo en teoría, pero sin
  evidencia propia todavía, no se cambió a ciegas.
- **D44 — Cobertura multi-idioma real, probada contra un sitio nuevo (no
  bodas.net).** Toda la validación hasta ahora fue contra un solo sitio, un
  solo CMP (OneTrust), un solo idioma (español). Contra decathlon.fr (Didomi,
  francés) salieron dos gaps reales:
  - `REJECT_TEXT_PATTERNS`/`ACCEPT_TEXT_PATTERNS` tenían inglés y español
    nada más. El botón real decía "Continuer sans accepter" — cero variantes
    en francés. Confirmado inspeccionando el DOM en vivo. Sumado FR, IT, PT
    (mercados de `localizador`) y DE (común en flujos de consentimiento UE)
    a las dos listas, simétrico.
  - `handle_consent` no esperaba nada entre iteraciones cuando no encontraba
    el banner — 3 pasadas casi instantáneas. En una página pesada (Didomi
    tarda en inyectar su popup tras cargar su SDK), las 3 pasadas terminaban
    antes de que el popup existiera. Cambiado a esperar la señal real
    (`page.wait_for_selector` sobre el contenedor) en vez de un sleep fijo
    adivinado — pero **medido, no asumido**: la primera versión esperaba
    2.5s por adelantado en cada página, y le costó al suite completo +137s
    (365s → 502s), porque la mayoría de páginas no tiene ningún CMP o ya lo
    tiene cargado cuando se chequea. Rediseñado a "segunda oportunidad":
    intenta rápido primero (como antes), y solo si esa primera pasada no
    encuentra nada, espera hasta 800ms antes de reintentar. Costo final
    medido: +44s sobre el suite completo (365s → 408.55s) — mucho más
    razonable para un beneficio que, para colmo, ni siquiera cierra el caso
    de abajo.
  - **Ninguno de los dos cerró el caso de decathlon.fr del todo**: el
    `wait_for_selector` sí encuentra *algo* ahí, pero no es el popup real de
    Didomi (`didomi-popup-*` nunca se adjunta al DOM en una corrida headless
    de Orpheus, aunque sí se ve en un browser interactivo normal — sospecha
    sin confirmar: el sitio o el SDK de Didomi suprime el popup para tráfico
    automatizado). No se persiguió más porque **no genera daño real**:
    verificado en vivo que ni el texto del banner se filtra a `text` ni el
    contenido real queda bloqueado (11.976 caracteres reales, `pageType:
    profile`, `structured.source: jsonld` con precio/rating — todo llegó
    bien). Documentado como límite conocido, no como bug abierto.

## Estado de los "problemas conocidos" del brief

- **P0 (requests + responses para entender qué dispara el contenido)** —
  **RESUELTO.** `netrec.py` captura requests y responses con bodies; el probe
  (`expand.py`) clickea/scroll y el recorder atrapa los XHR resultantes.
  Evidencia: `/faq` del fixture (contenido solo detrás de un XHR) aparece en
  `text` Y en `apiResponses` (ver `work/demo-faq.jsonl`).
- **P1 colapso por semántica** — RESUELTO (no depende de texto "ver más";
  `<details>`, `aria-expanded`, accordion). "Load more" sigue existiendo como
  fallback para listas infinitas, guarded.
- **P1 paginación de APIs internas** — RESUELTO (`apipage.py`). Evidencia:
  `/paginated` captura page=1 y rejuega page=2; page=3 (vacía) no se guarda.
- **P1 extractores por tipo de página** — RESUELTO (`extractors.py`, listing y
  profile). Evidencia en tests.
- **P2 suite de tests** — RESUELTO (13 tests deterministas offline).
- **P2 multimodal** — PARCIAL. `--export-images` guarda imágenes + campo
  `images` como puntero para Nemotron 3. El pipeline de análisis multimodal
  queda del lado del consumidor.

## Pendiente / limitaciones conocidas

- **Banners de consent en iframes cross-origin**: no se pueden tocar desde el
  frame principal. Fase 4 captura su texto vía GET polite (`frameTexts`), pero
  el banner no se puede rechazar.
- **Consent walls custom de SPAs (ej. ionos.es)**: su modal Svelte filtra su
  propio texto al markdown y no siempre se puede limpiar SIN aceptar. La regla
  "nunca aceptar" impide desbloquear la página; es una limitación inherente.
  Para el escenario 2 se usa un CMP estándar real (OneTrust en britannica.com).
- **Didomi en decathlon.fr no se dismissea** (ver D44) — el popup real nunca
  se adjunta al DOM en una corrida headless, causa sin confirmar. No
  bloqueante hoy (ni fuga de texto ni contenido perdido, verificado en vivo),
  pero si algún día un sitio con Didomi SÍ pierde contenido real detrás del
  popup, este es el primer lugar a mirar.
- `--export-images` requiere red y puede fallar en hosts que bloquean
  hotlinking.
- `replay_pagination` solo aplica a GET con params de paginación y cuerpos
  tipo lista; APIs sin esos shape quedan fuera (por diseño, YAGNI).
- Los bloqueadores anti-bot live son **intermitentes** (nowsecure.nl, ssense,
  bot.incapsula.com pasaron sin challenge en la validación). La prueba
  determinista del fail-closed es el fixture (`test_scenario4_*`), no un sitio
  live.
- Python 3.14 (uv) en este entorno; crawl4ai 0.9.2.

## Cómo correr

```bash
uv sync && uv run playwright install chromium
uv run gscrape --help

# 5 escenarios (live):
scripts/scenario1-simple.sh
scripts/scenario2-cookies.sh
scripts/scenario3-crawl.sh
scripts/scenario4-blocked.sh
scripts/scenario5-cache.sh

# Validación offline determinista (todos los escenarios + P0/P1):
scripts/fixture-check.sh
```

## Evidencia (output real, no "debería funcionar")

`uv run pytest tests/ -q` → **24 + structured + server tests en verde** (13 base +
normalización, dedupe de tracking, crawl concurrente, sitemap, cap de texto, retry 5xx,
summary, CSV, fail-closed stall, export de imágenes, extracción estructurada, e2e
server mode).

Fase 3 (output enriquecido): `uv run pytest tests/test_meta.py -q` → 16 passed (unit idioma/metadata, e2e `/meta-rich`, screenshot PNG, CSV `language`, CLI `--screenshots`).

Fase 4 (cobertura): `uv run pytest tests/test_coverage.py -q` → 16 passed (config/`to_dict`, e2e `/gated` contenido tras dismiss, e2e `/frames` reporte + `frameTexts` + redirect + robots-skip, e2e `/flaky403` retry acotado, regla 429 limpia, CSV `retries`, waitcontent unit).

### Validación live (con internet disponible, jun 2026)

- **Escenario 1** (`python.org`) → PASS. 8.487 chars de texto, sin error.
- **Escenario 2** (`britannica.com`, OneTrust) → PASS. 30.821 chars, **cero**
  ruido de consent. El accept nunca se clickeó.
- **Escenario 3** (`wikipedia.org/wiki/Web_scraping`, `--max-pages 5`) →
  PASS. 5 records, todos en `en.wikipedia.org`. 432 links descubiertos.
  (Antes del fix D13 daba 1 char de texto — `remove_overlay_elements` era el
  culpable.)
- **Escenario 4**: los 3 sitios de prueba (nowsecure.nl, ssense.com,
  bot.incapsula.com) **no desafiaron** el día de la validación (intermitente).
  El fail-closed se prueba determinista en el fixture: `/blocked` →
  `PROTECTION_BLOCKED` con texto vacío.
- **Escenario 5** (`python.org`, cache) → PASS. La 2ª corrida salió del cache.
- **Escenario 6 (server mode)** (`scripts/scenario-server.sh`, `python.org`) →
  PASS. Warm path (782 chars) vía `gscrape serve`, cache fast-path idéntico, y
  fallback a spawn con el mismo texto. Verificado live.
- **Sitemap + crawl live** (`--sitemap https://www.bbc.com/sitemap.xml
  --crawl --max-pages 3 --concurrency 2`) → PASS. Sitemapindex real seguido,
  3 records con `--max-text-chars 800` respetado y CSV con 4 filas.
- **Fail-closed live** (`https://demo.datadome.co`) → PASS tras D22:
  `PROTECTION_BLOCKED: no HTTP status and empty payload (bot challenge...)`.
  (Antes: record vacío sin error.)
- **Export de imágenes live** (`www.python.org --export-images`) → PASS: PNG
  real descargado (580x164). Wikipedia falla por hotlink protection (D24).

### Demo CLI local (fixture server, `work/demo-*.jsonl`)

- **cookie** → `text` = `"# Página de Acme\nEl contenido real que interesa al scraper.\n"`
  (cero ruido de consent). El accept nunca se clickeó (hits del server lo confirman).
- **blocked** → `{"error": "PROTECTION_BLOCKED: Cloudflare detected in server header",
  "protectionBlocked": true, "text": ""}`.
- **private** (robots) → `ROBOTS_BLOCKED`; con `--ignore-robots` → scrapea OK.
- **crawl --max-pages 3** → 3 records `/loop`,`/loop2`,`/loop3`, todos same-host.
- **cache** → 3 corridas de `/`, el server solo registró **2** hits de `/`
  (la 3ra salió del cache).
- **faq (P0)** → el contenido que solo vive tras un XHR aparece en `text` y en
  `apiResponses` (`/api/faq`).

### Consumo del output (downstream LLM)

El campo `text` es el que se envía a los LLMs (name estable, no cambia). Un lote
de URLs scrapeadas se procesa con cualquier herramienta de batch que lea JSONL
apuntando a `text` (p. ej. `python -c`/`jq` para filtrar por `summary` antes de
enviar, o el `--jsonl-field text` de la capa de batch). Nota: páginas grandes
(wikipedia, ~60KB) exceden el TPM de tiers bajos → para producción, recortar
`text` (`--max-text-chars`) o usar modelos con TPM alto.

### Bugs reales cazados en la validación live

- `remove_overlay_elements=True` → Wikipedia vaciaba (D13).
- Falso positivo de protección: "datadome" en prosa de un artículo bloqueaba
  Wikipedia (D14).
- `cf-ray` presente → bloqueaba sitios que solo usan Cloudflare como CDN (D14).
- `handle_consent` manipulando el DOM tras dismiss → rompía la app Svelte de
  IONOS (D15).
- Los scripts de escenario imprimían "PASS" aunque el check fallara → ahora
  salen con exit != 0.