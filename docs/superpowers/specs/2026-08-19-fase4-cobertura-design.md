# Spec — Fase 4: Cobertura (consent walls SPA, iframes cross-origin, anti-bot intermitente)

- **Fecha**: 2026-08-19
- **Estado**: aprobado (diseño), implementado
- **Repo**: `Orpheus` (`growth-scraper`)
- **Consumidores afectados**: record JSON (campos aditivos), CSV (solo `retries`), cliente de referencia (contrato intacto: sigue leyendo solo `text`/`error`)

## 1. Contexto y problema

Tres huecos de cobertura detectados en sitios reales:

1. **Consent walls SPA con contenido gated** — algunas webs muestran un wall y el
   contenido real **no entra al DOM hasta que descartas el consent**. Hoy capturamos
   el HTML justo después de rechazar/ocultar → el texto sale incompleto.
2. **Iframes cross-origin** — parte del contenido vive dentro de iframes de otro
   dominio (embeds, mapas, formularios). Hoy no se reportan ni se captura su texto.
3. **Anti-bot intermitente** — algunos sitios a veces sirven la página y a veces
   devuelven 403/429. Hoy cualquier 403/429 con anti-bot es bloqueo permanente
   fail-closed, sin reintento → perdemos páginas que en realidad se podían leer.

## 2. Objetivos

- **O1** — Esperar (acotado) a que cargue el contenido gated tras descartar el
  consent, para que el texto salga completo. Fail-open.
- **O2** — Reportar los iframes del HTML (`frames`) y capturar el texto del `src`
  (`frameTexts`) con un GET de solo lectura, polite y acotado.
- **O3** — Reintentar 403/429 con tope y backoff largo + jitter, registrando la
  evidencia en el record. Sin evadir, sin martillear.
- **O4** — Suite offline determinista ampliada, con los tests actuales en verde.

## 3. No-objetivos (fuera de alcance de esta fase)

- Evasión de anti-bot (nada de resolver challenges, rotar IPs, falsificar UA).
- Aceptar consent (seguimos reject-only).
- `robots.txt` disallowed: sigue siendo hard stop, sin reintentos.
- Acceder al DOM de iframes cross-origin (same-origin policy no se vulnera; solo se
  hace un GET al `src`).
- Cambios en el contrato del cliente de referencia ni en los campos que ya lee.

## 4. Decisiones clave

- **D34 — Contenido gated tras dismiss.** Tras una acción de consent
  (rejected/hidden/removed), esperar con polling acotado (`consent_wait_ms`, default
  5000, `0` = off) a que el texto del body se estabilice (Δ < 5% en 2 polls). Sin
  acción de consent → no se espera. Fail-open.
- **D35 — Iframes: reportar + fetch del src.** `frames` = inventario (src absoluto,
  title, `crossOrigin`). `frameTexts` = texto del `src` vía GET de solo lectura,
  respetando robots, timeout, cap 5 iframes, texto truncado a 2000 chars, fail-open
  por frame.
- **D36 — Anti-bot intermitente: retry 403/429 con tope.** `anti_bot_retries`
  (default 2) reintentos extra con `anti_bot_backoff_s` (default 15s) + jitter.
  5xx sigue con el backoff corto actual. `robots.txt` disallowed nunca reintenta.
  `Record.retries` registra cuántos reintentos hubo.

## 5. Diseño detallado

### 5.1 `src/growth_scraper/waitcontent.py` (nuevo)

**`async wait_for_content(page, cfg) -> str`**
- Mide `document.body.innerText.length` cada ~500ms.
- Para pronto si el texto se estabiliza (Δ < 5% en 2 polls consecutivos).
- Límite duro: `cfg.consent_wait_ms` (default 5000ms; `0` = no espera).
- Devuelve un resumen corto (`"wait 2.1s delta +34%"`, `"timeout"`, `"error"`).
- Nunca lanza. No toca el DOM (solo lee).

**Wiring:** en `_hook_after_goto`, justo después de `handle_consent`, si el resultado
del consent fue `rejected*`/`hidden*`/`removed*` → `await wait_for_content(...)`. Si
el consent no hizo nada → no se espera (foco y cero coste en sitios sin consent).

### 5.2 `src/growth_scraper/iframes.py` (nuevo)

**`extract_iframes(url: str, html: str) -> list[dict]`** (puro, fail-open)
- Parsea `<iframe>` del HTML crudo: `src` (absoluto via `urljoin`), `title`,
  `crossOrigin` (bool: `netloc` del src ≠ `netloc` de la página).
- Descarta `src` vacío y `data:`/`about:`/`blob:`. Cap `max_frames` (default 5).

**`async fetch_frame_texts(srcs: list[str], cfg, robots) -> list[dict]`**
- Por src único, en orden, con concurrencia acotada (máx 3):
  - Si `robots.is_allowed(src)` es falso → se salta (sin request).
  - GET con httpx (timeout 15s, follow_redirects).
  - Texto = texto visible del HTML fetcheado (BeautifulSoup `get_text`), truncado a
    ~2000 chars.
- Fail-open por frame (un error → ese frame se omite, nunca rompe el record).

**Wiring:** en `run_one`, tras construir el record: `record.frames =
extract_iframes(url, raw_html)`; si `cfg.fetch_frames` y hay frames →
`record.frameTexts = await fetch_frame_texts(...)`. `frameTexts` solo incluye los
fetches con éxito.

### 5.3 Anti-bot intermitente en `run_one`

- El bucle de reintentos actual (`1 + cfg.max_retries`) retry en `status >= 500` y
  excepciones. Se amplía: **403/429 también reintentan** hasta `cfg.anti_bot_retries`
  adicionales, con backoff `anti_bot_backoff_s` + jitter (el 5xx conserva su backoff
  corto).
- Tope duro total: `1 + max_retries + anti_bot_retries` intentos. Nunca más.
- `robots.txt` disallowed → retorno inmediato sin reintento (sin cambios).
- Al terminar: `record.retries = <número real de reintentos>` (0 si no hubo).

### 5.4 Config y record (nuevos campos)

Config (`ScrapeConfig`):
- `consent_wait_ms: int = 5000`
- `fetch_frames: bool = True`
- `max_frames: int = 5`
- `anti_bot_retries: int = 2`
- `anti_bot_backoff_s: float = 15.0`

Record:
- `frames: Optional[list] = None`
- `frameTexts: Optional[list] = None`
- `retries: int = 0`
- `to_dict()` los emite de forma incondicional (como `structured`/`meta`).
- CSV: solo se añade la columna `retries`.

CLI:
- `--no-frames` → `fetch_frames=False`.
- `--consent-wait-ms N` y `--anti-bot-retries N`/`--anti-bot-backoff N` opcionales.
- Sin flags para screenshots/iframes en el cliente de referencia → defaults: cero
  coste extra en sitios sin consent/iframes/anti-bot.

### 5.5 Fixtures

Nuevas rutas en `tests/fixtureserver.py`:
- `/gated` — página con wall de consent que al "rechazar" libera un `<p>` con
  contenido adicional tras ~800ms (JS con `setTimeout`). E2E: con
  `consent_wait_ms` alto el texto sale completo; con 0 sale corto.
- `/frames` — página con un iframe cross-origin apuntando a `http://127.0.0.1:<otro
  puerto>/frame-content` (segundo server de fixture) y otro iframe a `about:blank`
  (descartado). E2E: `frames` tiene 1 entrada con `crossOrigin=True`,
  `frameTexts` trae el texto del src.
- `/flaky403` — ruta que devuelve 403 en los 2 primeros hits y 200 después
  (anti-bot intermitente). E2E: con `anti_bot_retries=2` y backoff bajo el run
  acaba en éxito con `retries>=1`; con `anti_bot_retries=0` acaba bloqueado.

### 5.6 Tests

- Unit: `extract_iframes` (urljoin, crossOrigin, filtros `data:`/vacío, cap),
  `wait_for_content` no es testeable sin browser → se cubre en e2e.
- e2e (nuevo `tests/test_coverage.py`): los tres escenarios de fixtures por browser,
  vía `conftest` helpers.
- Regresión: suite completa en verde (63 actuales + nuevos).

## 6. Verificación

- `uv run pytest tests/ -q` → todos en verde.
- `bash scripts/fixture-check.sh` → PASS.
- Smoke live (si hay internet): `uv run gscrape <url-con-consent> -o /tmp/x.jsonl`
  → texto completo tras dismiss; `<url-con-embeds>` → `frames`/`frameTexts`.
- Contrato del cliente: `curl http://127.0.0.1:8743/scrape` (server) → exit 0 + texto;
  server caído → spawn fallback (regresión del cliente de referencia).

## 7. Consumidores afectados

- Record JSON: campos aditivos (`frames`, `frameTexts`, `retries`).
- CSV: columna nueva `retries`.
- Cliente de referencia: contrato intacto (solo lee `text`/`error`).