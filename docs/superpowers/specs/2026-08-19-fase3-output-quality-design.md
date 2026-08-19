# Spec — Fase 3: Calidad de output (idioma, metadata rica, screenshots)

- **Fecha**: 2026-08-19
- **Estado**: aprobado (diseño), a la espera de plan de implementación
- **Repo**: `growth-scraper` (`~/.claude/scripts/growth-scraper`)
- **Consumidores afectados**: record JSON (todas las tools), CSV mirror, `orpheus.sh` (contrato intacto), TS wrappers (`tryOrpheus` intactos)

## 1. Contexto y problema

Las Fases 1+2 (server mode + extracción estructurada) dejaron el record con `text`,
`structured`, `apiResponses` e `images`. Para análisis de mercado falta:

1. **Idioma** — no hay forma de filtrar/segmentar records por idioma sin parsear el
   texto. Un listing puede mezclar mercados.
2. **Metadata rica** — `summary` solo expone title/metaDescription/h1. Los OG tags,
   canonical, autor, fecha de publicación y favicon existen en el HTML pero no se
   exponen.
3. **Evidencia visual** — el pipeline multimodal (Nemotron 3) lee imágenes, pero el
   scraper no captura screenshots (solo `images` de `<img>`).

## 2. Objetivos

- **O1** — Detectar el idioma de cada record (ISO 639-1) sin dependencias nuevas,
  fail-open, y exponerlo en `summary` (triage) + CSV.
- **O2** — Extraer metadata rica del HTML crudo (OG, canonical, autor, publishedAt,
  favicon, twitterCard) en un campo `meta` de shape estable.
- **O3** — Capturar screenshot full-page opcional por página (CLI) como puntero
  multimodal, en un campo `screenshots` con rutas a disco.
- **O4** — Suite offline determinista ampliada, con los tests actuales en verde.

## 3. No-objetivos (fuera de alcance de esta fase)

- OCR ni extracción de texto desde el screenshot.
- Traducción del contenido.
- Cambios en `structured.py`, server mode, ni en el contrato de `orpheus.sh`.
- Screenshots vía server (escribe a disco del server; se queda en CLI, igual que
  `--export-images`).
- Base64 inline de screenshots en el record (payload autocontenido pero grande).

## 4. Decisiones clave

- **D30 — Idioma fail-open sin dependencias.** `<html lang>` → `<meta
  http-equiv=content-language>`/`<meta name=language>` → stopwords por idioma sobre
  `text` (solo si `text` ≥ ~80 palabras y el ganador supera un umbral claro). Sin
  señal → `None`. Cero deps nuevas.
- **D31 — `meta` es un campo de shape estable** (keys presentes con `null` si
  ausentes), separado del triage de `summary` (solo `language` ahí y en CSV).
- **D32 — Screenshots solo CLI.** `--screenshots DIR` escribe PNG full-page en disco
  y `record.screenshots` lleva rutas (puntero multimodal, igual que `images`). El
  server nunca escribe a disco.
- **D33 — Captura en el hook `before_retrieve_html`** (página viva y expandida). El
  path se propaga por `session.screenshot_path` → `record.screenshots` solo si el run
  termina en éxito. En retries el hook re-captura; vale el path del intento exitoso.

## 5. Diseño detallado

### 5.1 `src/growth_scraper/meta.py` (nuevo, módulo puro)

**`detect_language(html: str, text: str) -> str | None`**
- Orden: `<html lang>` → `<meta http-equiv=content-language>` → `<meta
  name=language>` → clasificación por stopwords sobre `text`.
- Stopwords por idioma (constantes en `config.py`, `LANG_STOPWORDS`: es, en, pt, fr,
  de, it; se puede ampliar). Score = conteo de stopwords normalizado por palabras
  totales; gana el idioma con mayor ratio si supera un umbral y el segundo queda a
  distancia.
- `text` < ~80 palabras o sin ganador claro → `None`.

**`extract_meta(html: str, url: str) -> dict`**
- Keys (shape estable): `canonical`, `ogTitle`, `ogDescription`, `ogImage`,
  `twitterCard`, `author`, `publishedAt`, `favicon`. Ausentes → `None`.
- Fuentes: `link[rel=canonical]`, `meta[property=og:*]`, `meta[name=twitter:card]`,
  `meta[name=author]`/`meta[property=article:author]`,
  `meta[property=article:published_time]`/`meta[name=date]`,
  `link[rel~=icon]`.
- URLs relativas → absolutas con `urljoin(url, href)`.
- Fail-open: cualquier error → dict con keys `None`, nunca lanza.

### 5.2 `src/growth_scraper/screenshot.py` (nuevo)

**`async capture_screenshot(page, url: str, out_dir: str) -> str | None`**
- `page.screenshot(full_page=True)`, archivo `{host}-{YYYYmmdd-HHMMSS}-{hash8}.png`
  en `out_dir` (creado si no existe).
- Devuelve la ruta absoluta; cualquier error → `None` (nunca rompe el record).

### 5.3 Pipeline (hook + run_one)

- `ScrapeConfig.screenshot_dir: str | None = None`.
- `_hook_before_retrieve_html`: si `cfg.screenshot_dir`, `session.screenshot_path =
  await capture_screenshot(page, url, cfg.screenshot_dir)`.
- `run_one`, en éxito: `if cfg.screenshot_dir and used_session and
  used_session.screenshot_path: record.screenshots = [used_session.screenshot_path]`.

### 5.4 Record / summary / CSV

- `Record.meta: Optional[dict] = None`, `Record.screenshots: Optional[list] = None`.
- `to_dict()`: `"meta": self.meta`, `"screenshots": self.screenshots` (incondicional,
  igual que `structured`).
- `summary`: `language` solo cuando se detecta (ausente si `None`).
- CSV: columna `language` (solo esa; `meta` no se vuela al CSV).

### 5.5 CLI / server

- `cli.py`: flag `--screenshots DIR` → `cfg.screenshot_dir`.
- Server: sin cambios funcionales. Los records de `/scrape` llevan `meta` y
  `screenshots` (nulos) porque vienen de `to_dict()`; el server nunca captura
  screenshots (D32).

### 5.6 Fixtures

- Nueva ruta `/meta-rich`: `<html lang="es">`, `og:title/description/image`,
  `canonical`, `meta author`, `article:published_time`, favicon. No se tocan las
  rutas existentes.

### 5.7 Tests

- Unit `tests/test_meta.py`:
  - `detect_language`: attr lang, meta content-language, fallback stopwords,
    `None` en vacío y en texto corto.
  - `extract_meta`: campos poblados, `urljoin` en relativa, fail-open en HTML roto.
- e2e (append a `test_meta.py`):
  - `/meta-rich` por browser → `record.meta` poblado + `summary.language == "es"`.
  - Screenshot: run con `cfg.screenshot_dir` → `record.screenshots` con 1 ruta,
    archivo existe y empieza con magic bytes PNG.
- CSV: columna `language` en header y row.
- Regresión: `uv run pytest tests/ -q` → 47 actuales + nuevos en verde.

## 6. Verificación

- `uv run pytest tests/ -q` → todos en verde.
- `bash scripts/fixture-check.sh` → PASS.
- `bash scripts/scenario-server.sh` (live, si hay internet) → PASS (regresión del
  server mode).
- Smoke: `uv run gscrape https://www.python.org -o /tmp/x.jsonl --screenshots
  /tmp/shots` → record con `meta`, `summary.language == "en"` y screenshot en disco.

## 7. Consumidores afectados

- Record JSON: campos nuevos aditivos (`meta`, `screenshots`, `summary.language`).
- CSV: columna nueva `language`.
- `orpheus.sh` / `tryOrpheus`: contrato intacto.