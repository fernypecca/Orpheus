# Spec — Modo servidor (`gscrape serve`) + Extracción estructurada (`structured`)

- **Fecha**: 2026-08-18
- **Estado**: aprobado (diseño), a la espera de plan de implementación
- **Repo**: `growth-scraper` (`~/.claude/scripts/growth-scraper`)
- **Consumidores afectados**: `orpheus.sh`, storefront-analyzer (`tryGscrape`), landing-qa-tool

## 1. Contexto y problema

El scraper es maduro y $0 (local, Crawl4AI/Playwright), pero tiene dos dolores reales:

1. **Integración cara**: cada fetch de una tool TS spawnea `uv run gscrape` (arranque
   frío de uv + Playwright) con timeout de 90s. Para análisis multi-URL (un listing +
   N profiles) el coste de arranque se multiplica.
2. **Payload pobre para análisis**: storefront-analyzer parsea `text` crudo para sacar
   precio/rating/categoría. Los datos estructurados (schema.org, microdata, meta) existen
   en el HTML pero el record no los expone.

Este spec añade (a) un **server mode** con browser caliente y cache compartido, y (b) un
**extractor estructurado** schema-aware. Ambas fases se diseñan juntas; la cobertura de
SPAs y screenshots quedan fuera de alcance.

## 2. Objetivos

- **O1** — `gscrape serve`: daemon local (127.0.0.1) que reutiliza un único
  `AsyncWebCrawler` y sirve single-URL scrapes por HTTP, con cache fast-path y control
  de concurrencia.
- **O2** — `orpheus.sh` híbrido: intenta el server primero; si no responde, spawnea
  `uv run gscrape` como hoy. Contrato de salida sin cambios. Wrappers TS intactos.
- **O3** — `structured.py`: extrae entidades estructuradas del HTML crudo
  (JSON-LD → microdata → meta/OG → heurística) y las expone en el record, en `summary`
  (triage barato) y en el CSV mirror.
- **O4** — Suite de tests offline determinista ampliada (fixture + server), con los 24
  tests actuales en verde.

## 3. No-objetivos (fuera de alcance de esta iteración)

- Fase 3 (detección de idioma, screenshots, metadata rica) y Fase 4 (cobertura de
  SPA consent walls / iframes cross-origin / anti-bot intermitente).
- Server mode multi-página (`--crawl`, `--sitemap`, `--batch`, `--concurrency`).
  El server es single-URL por diseño (YAGNI para el caso de uso del wrapper).
- `--export-images` en el server (escribe a disco del server; se queda en CLI).
- Evasión anti-bot / CAPTCHA (guardrail existente, no se toca).
- Cambios en el contrato de salida de `orpheus.sh` ni en `tryOrpheus`/`tryGscrape`.

## 4. Decisiones clave

- **D25 — uvicorn es dueño del event loop.** El server no llama `asyncio.run` por
  request; el crawler se crea/cierra en el lifespan de la app (compatible con D7:
  start→crawl→close en el mismo loop).
- **D26 — Sesión por request** (D18). Cada `POST /scrape` crea su `Session` y hace
  `kill_session` al terminar, aunque falle.
- **D27 — 200 siempre que el scrape corra.** `protectionBlocked`/`error` viven en el
  record (igual que CLI). Errores de transporte/contrato → 400 (body inválido) o 500
  (excepción interna). El cliente decide qué hacer con el record.
- **D28 — `structured` fail-open.** Cualquier error o HTML sin señales → `structured:
  null`. Nunca rompe el record.
- **D29 — Server single-URL y solo loopback.** Documentar SSRF: no exponer en interfaz
  pública. `--token` opcional para capa extra de seguridad local.

## 5. Spec detallado

### 5.1 `gscrape serve` (server mode)

**CLI**: nuevo subcomando detectado en `cli.py` antes del parseo de `urls`:

```bash
uv run gscrape serve [--port 8743] [--cache-dir DIR] [--max-concurrency 4] [--token TOKEN] [-v]
```

- Host fijo `127.0.0.1` (no configurable).
- Puerto por defecto `8743`, sobreescribible con `--port` o env `GSCRAPE_PORT`.
- `--cache-dir` opcional: si está, `POST /scrape` con URL cacheada responde de inmediato
  el record con `fromCache: true` (fast path, sin tocar el browser).
- `--token` opcional: si se setea, todo request debe llevar `Authorization: Bearer <token>`.
- `-v`: logs a stderr.

**Módulo nuevo** `src/growth_scraper/server.py`:

- `create_app(cache_dir, max_concurrency, token) -> FastAPI`.
- Lifespan: startup → `AsyncWebCrawler().start()`; shutdown → `crawler.close()` +
  kill de sessions pendientes.
- `asyncio.Semaphore(max_concurrency)` alrededor de cada `run_one`.
- Endpoints:
  - `GET /health` → `{status: "ok", version}`.
  - `POST /scrape` → body `{url: str, options?: {...}}`, respuesta = record (dict).
- Options admitidas (mismas semánticas que los flags CLI):
  `maxTextChars`, `delay`, `jitter`, `ignoreRobots`, `noConsent`, `noExpand`,
  `noApis`, `fitText`, `maxRetries`, `maxApiResponses`, `pageTimeout`, `cacheDir`.
  Se mapean a la config del pipeline; campos desconocidos se ignoran con warning.
- Respuesta de error de contrato: `400` si falta `url`/no es string/URL inválida;
  `500` si el scrape lanza excepción no esperada. El cuerpo es `{error: str}`.
- El server devuelve el **mismo shape de record** que la CLI (para que `orpheus.sh`
  reutilice el tail de parsing).

### 5.2 `orpheus.sh` híbrido

Flujo nuevo (delante de la ruta actual, que queda intacta como fallback):

1. Si `GSCRAPE_SKIP_SERVER=1` o hay `--` flags pasados (`PASS` no vacío, pueden ser
   solo-CLI) → **ir directo a spawn**.
2. `curl -sS --max-time "$ORPHEUS_TIMEOUT_S" -X POST -H 'Content-Type: application/json'`
   a `http://127.0.0.1:${GSCRAPE_PORT:-8743}/scrape` con
   `{"url":"$URL","options":{"maxTextChars":$MAX_CHARS,"delay":0.3,"jitter":0.15}}`
   (env `GSCRAPE_PORT` para override).
3. Si curl falla (connection refused, timeout, exit != 0, HTTP != 200) o el body no
   parsea como record (dict con clave `url`) → **fallback a spawn** (ruta actual).
4. Si el server responde un record → pasa por el **mismo tail de parsing actual**
   (protectionBlocked/error → exit 1 + mensaje stderr; si no, imprime `text` → exit 0).
5. `--out FILE` escribe el record JSONL también en la ruta server.

Contrato de salida **sin cambios**: exit 0 + texto limpio en stdout / exit 1 + mensaje
stderr (el caller cae a Firecrawl). `tryOrpheus`/`tryGscrape` de storefront-analyzer y
landing-qa-tool **no se tocan**.

> Nota: `--max-time` usa `ORPHEUS_TIMEOUT_S` (90) porque `/scrape` puede tardar hasta
> `page_timeout` + retries. El "connection refused" es inmediato, así que el fallback
> rápido sigue funcionando cuando el server está caído.

### 5.3 `structured.py` (extracción estructurada)

**Módulo nuevo** `src/growth_scraper/structured.py`:

- `extract_structured(html: str, summary: dict) -> dict | None`.
- Entrada: HTML crudo (`result.html`) + summary (h1/title/metaDescription).
- Nunca lanza: cualquier error o HTML sin señales → `None`.

**Pipeline de detección (prioridad, primera fuente no-vacía gana por campo)**:

1. **JSON-LD**: todos los `script[type="application/ld+json"]`. Tolerar arrays,
   `@graph`, `@id`; coercion string↔number; cap de tamaño (≤ 1 MB acumulado) para
   evitar abuso. Tipos de entidad objetivo:
   - profile/product: `Product`, `LocalBusiness`, `ProfessionalService`, `Service`,
     `Organization`, `Place`
   - listing: `ItemList`, `CollectionPage`, `OfferCatalog`, `BreadcrumbList` (categoría)
2. **Microdata**: `itemscope`/`itemprop` con los mismos tipos.
3. **Meta/OG**: `meta[property=og:*]` + `meta[name=description]` +
   `meta[name=price]/[name=currency]` (si existen).
4. **Heurística**: selectores en `config.py` (multi-idioma, como la blacklist de
   clickguard), solo para los campos que schema/meta no cubran: precio, rating,
   reviewCount, categoría (breadcrumb), teléfono, email, dirección.

**Shape de salida** (campo `structured` en el record):

```jsonc
{
  "entityType": "profile" | "product" | "listing" | null,
  "source": "jsonld" | "microdata" | "meta" | "heuristic" | null,
  "name": "string | null",
  "description": "string | null",
  "image": "string | null",
  "price": { "value": "string | null", "currency": "string | null", "isRange": false },
  "rating": { "value": 4.8, "best": 5, "count": 127 },
  "reviews": [ { "author": "string", "rating": 5, "text": "string" } ],   // cap 3
  "category": "string | null",
  "contact": { "phone", "email", "website", "address": { "street", "locality", "region", "postalCode", "country" } },
  "itemCount": 24
}
```

- `source` = la **primera fuente en orden de prioridad** (jsonld > microdata > meta >
  heuristic) que aportó ≥1 campo (triage de confianza determinista).
- `reviews`: primeras 3 en orden de documento, con `rating` como float o null.
- `price.value` es string (permite rangos como "€50–€200"); `isRange` lo marca.
- `entityType` lo decide el tipo de entidad más específico encontrado
  (profile/product > listing; si no hay entidad pero hay `itemCount` heurístico →
  `listing`).

**Integración**:

- `pipeline.py`: tras construir el record, `structured = extract_structured(html,
  summary)` y `record.structured = structured`.
- `_build_summary` (pipeline.py:140) añade campos de triage barato:
  `structuredSource`, `structuredPrice`, `structuredRatingValue`,
  `structuredReviewCount`, `structuredCategory` (solo si `structured` existe).
- `records.py` `CsvWriter.HEADERS` + `write()` añaden esas 5 columnas (empty si null).
- `config.py` `Record` dataclass: añadir campo `structured` (dict | None), default None.

### 5.4 Config (`config.py`)

- Constantes nuevas de selectores heurísticos multi-idioma (prefijo `STRUCTURED_`):
  `STRUCTURED_PRICE_SELECTORS`, `STRUCTURED_RATING_SELECTORS`,
  `STRUCTURED_REVIEW_COUNT_SELECTORS`, `STRUCTURED_CATEGORY_SELECTORS`,
  `STRUCTURED_PHONE_SELECTORS`, `STRUCTURED_EMAIL_SELECTORS`,
  `STRUCTURED_ADDRESS_SELECTORS`.
- Defaults sensatos (ej. `[itemprop=price]`, `.price`, `[data-price]`, `.rating`,
  `[aria-label*="stars"]`, breadcrumbs `[itemprop=itemListElement]`, `.breadcrumb`,
  tel/email regex sobre el HTML visible).

### 5.5 Testing

**Fixture server** (`tests/fixtureserver.py`), rutas nuevas, deterministas y offline:

- `/structured-jsonld` — JSON-LD completo (LocalBusiness con `aggregateRating`,
  `review[]` de 5, `offers.price`, `address`, contacto).
- `/structured-microdata` — itemscope/itemprop equivalente.
- `/structured-meta` — solo og: + meta name.
- `/structured-heuristic` — sin schema; `.price`, `[class*="rating"]`, breadcrumb.
- `/structured-none` — página plana → `structured: null`.

**Tests nuevos**:

- `test_structured.py`:
  - Prioridad de fuentes (jsonld > microdata > meta > heuristic) por ruta.
  - JSON-LD: `@graph`, array de reviews → cap 3, coercion string↔number.
  - `entityType` (profile/product/listing), `source`.
  - `price.currency` + `isRange`; rating value/best/count.
  - `/structured-none` → `structured` null; HTML roto → no lanza, null.
  - CSV: columnas nuevas presentes y pobladas.
- `test_server.py`:
  - `GET /health` → 200 `{status:"ok"}`.
  - `POST /scrape` contra fixture → 200, record con `text` no vacío.
  - `/blocked` (fixture) → 200, record con `protectionBlocked: true`.
  - `/private` (fixture, robots) → 200, record `ROBOTS_BLOCKED`.
  - Cache fast-path: `--cache-dir` → 2ª llamada responde con `fromCache: true` y el
    fixture no registra un 2º hit de la URL.
  - Body inválido (sin `url`) → 400. Con `--token` seteado y sin header → 401.
- **Los 24 tests actuales siguen en verde** (`uv run pytest tests/ -q`).
- `scripts/fixture-check.sh` → PASS (no debe romper los 5 escenarios).
- `scripts/scenario-server.sh` (live): levanta `gscrape serve` en background →
  `orpheus.sh python.org` (ruta server, exit 0 + texto), 2ª corrida con cache
  (`fromCache`), matar el server → `orpheus.sh` cae a spawn y devuelve lo mismo.

## 6. Criterios de éxito / verificación

1. `uv run pytest tests/ -q` → todos en verde (24 actuales + `test_structured` +
   `test_server`).
2. `scripts/fixture-check.sh` → PASS.
3. `scripts/scenario-server.sh` → PASS (ruta server, cache, fallback a spawn).
4. Live: `orpheus.sh https://www.python.org` con server arriba → texto en stdout,
   exit 0; con server abajo (o `GSCRAPE_SKIP_SERVER=1`) → mismo texto vía spawn.
5. Live: página de proveedor con schema (JSON-LD o microdata) → el record incluye
   `structured` con price/rating/category y el CSV con las columnas nuevas.

## 7. Archivos afectados

| Archivo | Cambio |
|---|---|
| `src/growth_scraper/server.py` | **nuevo** — app FastAPI + endpoints + lifespan |
| `src/growth_scraper/structured.py` | **nuevo** — extractor estructurado |
| `src/growth_scraper/cli.py` | subcomando `serve` |
| `src/growth_scraper/config.py` | selectores heurísticos `STRUCTURED_*`, campo `structured` en `Record`, port default |
| `src/growth_scraper/pipeline.py` | hook `extract_structured` + campos triage en `_build_summary` |
| `src/growth_scraper/records.py` | columnas CSV nuevas |
| `~/.claude/scripts/orpheus.sh` | ruta server-híbrida + fallback |
| `tests/fixtureserver.py` | rutas `/structured-*` |
| `tests/test_structured.py` | **nuevo** |
| `tests/test_server.py` | **nuevo** |
| `scripts/scenario-server.sh` | **nuevo** (live) |
| `README.md` / `AGENTS.md` | documentación: `serve`, `structured`, wrapper híbrido |

## 8. Preguntas abiertas

Ninguna. Las decisiones de detalle (port, nombres de selectores, cap de reviews) son
implementables por defaults del spec; cualquier desviación se documenta en el plan.
