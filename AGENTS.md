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
| `crawl.py` | BFS mismo-dominio con tope. Descubrimiento de links desde el HTML crudo. |

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
  frame principal (y no se capturan en `text`, porque `process_iframes=False`).
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

`uv run pytest tests/ -q` → **13 passed** (simple, banner cookies, crawl cap +
dominio, protección fail-closed, cache, P0 XHR, P1 paginación, extractores,
guard, robots).

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

### Bugs reales cazados en la validación live

- `remove_overlay_elements=True` → Wikipedia vaciaba (D13).
- Falso positivo de protección: "datadome" en prosa de un artículo bloqueaba
  Wikipedia (D14).
- `cf-ray` presente → bloqueaba sitios que solo usan Cloudflare como CDN (D14).
- `handle_consent` manipulando el DOM tras dismiss → rompía la app Svelte de
  IONOS (D15).
- Los scripts de escenario imprimían "PASS" aunque el check fallara → ahora
  salen con exit != 0.