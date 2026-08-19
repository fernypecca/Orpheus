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
  `orpheus.sh` intenta el server primero y cae a spawn si no responde.
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

### Integración downstream (llm-batch.py)

`python3 ~/.claude/scripts/llm-batch.py -i <jsonl> --jsonl-field text -p @prompt.md -o out.jsonl`
lee nuestro campo `text` y procesa el lote real contra Groq/NVIDIA (claves en
`~/.claude/.env`). Probado: 3/3 ok con groq `openai/gpt-oss-20b` y nvidia
(Nemotron). Nota: páginas grandes (wikipedia, ~60KB) exceden el TPM de tiers
bajos → para producción, recortar `text` o usar modelos con TPM alto.

### Consumo downstream (wrappers estandarizados)

Regla global: **Orpheus first, Firecrawl fallback** — no reinventar el shell-out.

- Shell universal: `~/.claude/scripts/orpheus.sh <url> [--max-chars N] [--out FILE] [-- <flags>]`
  → texto limpio en stdout, exit 0; exit 1 + mensaje en stderr (caer a Firecrawl).
  Usa `ORPHEUS_DIR` (default `~/.claude/scripts/growth-scraper`), `ORPHEUS_TIMEOUT_S`
  y `GSCRAPE_PORT`; server mode primero, fallback a spawn.
- TS: `tryOrpheus(url, maxChars)` en `lib/orpheus.ts` (shell-out vía `ORPHEUS_DIR`
  o hosted vía `ORPHEUS_URL`). Null = fail-closed, el caller cae al siguiente tier.
- No aplica a: APIs JSON/XML (RSS, Meta GraphQL) — usar `fetch`; sitios protegidos
  (Meta Ad Library) — usar Playwright. CLIs conocidos: G2 (Cloudflare),
  Trustpilot/Google News (robots.txt).

### Bugs reales cazados en la validación live

- `remove_overlay_elements=True` → Wikipedia vaciaba (D13).
- Falso positivo de protección: "datadome" en prosa de un artículo bloqueaba
  Wikipedia (D14).
- `cf-ray` presente → bloqueaba sitios que solo usan Cloudflare como CDN (D14).
- `handle_consent` manipulando el DOM tras dismiss → rompía la app Svelte de
  IONOS (D15).
- Los scripts de escenario imprimían "PASS" aunque el check fallara → ahora
  salen con exit != 0.