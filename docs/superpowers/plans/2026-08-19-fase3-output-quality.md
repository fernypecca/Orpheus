# Fase 3: Calidad de output (idioma, metadata rica, screenshots) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect per-record language (ISO 639-1) with a dependency-free heuristic, extract rich page metadata into a stable `meta` field, and capture optional full-page screenshots as a CLI-only multimodal pointer.

**Architecture:** A pure `meta.py` module (`detect_language` + `extract_meta`, fail-open, unit-testable without a browser) and a `screenshot.py` capture helper. The pipeline wires them: `record.meta`, `summary.language`, and `record.screenshots`. Screenshots are captured in the `before_retrieve_html` hook (page alive + expanded) and propagate through `session.screenshot_path` to the record only on a successful run. A new `--screenshots DIR` CLI flag and a `language` CSV column round it out. The server never writes screenshots to disk.

**Tech Stack:** Python 3 (stdlib + `bs4`, already a dependency), Playwright via crawl4ai, pytest, uv.

---

### Task 1: `meta.py` — language detection + rich metadata (pure module) + unit tests

**Goal:** A dependency-free, fail-open module that detects ISO 639-1 language and extracts rich metadata from raw HTML.

**Files:**
- Create: `src/growth_scraper/meta.py`
- Modify: `src/growth_scraper/config.py` (add `LANG_STOPWORDS`)
- Test: `tests/test_meta.py` (unit tests only — e2e comes in Task 3)

**Acceptance Criteria:**
- [ ] `detect_language(html, text)` returns `str | None`: `<html lang>` → `content-language` meta → `name=language` meta → stopword classification of `text` (≥80 tokens, best ratio ≥ 0.05, best ≥ 1.5× second)
- [ ] `extract_meta(html, url)` returns stable-shape dict with keys `canonical, ogTitle, ogDescription, ogImage, twitterCard, author, publishedAt, favicon`, all `None` when absent; relative URLs resolved via `urljoin`
- [ ] Both never raise on empty/broken HTML
- [ ] `LANG_STOPWORDS` defined in `config.py` for es/en/pt/fr/de/it

**Verify:** `uv run pytest tests/test_meta.py -q` → all pass

**Steps:**

- [ ] **Step 1: Write the failing unit tests**

`tests/test_meta.py`:

```python
"""Unit tests for meta.py (pure HTML parsing, no browser)."""

from growth_scraper.meta import detect_language, extract_meta

LANG_ATTR_HTML = '<html lang="es"><head><title>Acme</title></head><body><h1>Hola</h1></body></html>'

CONTENT_LANG_HTML = '''<html><head><meta http-equiv="content-language" content="fr-FR"></head>
<body><h1>Bonjour</h1></body></html>'''

NAME_LANG_HTML = '<html><head><meta name="language" content="de"></head><body><h1>Hallo</h1></body></html>'

EN_TEXT = ("The quick brown fox jumps over the lazy dog in the park and this is a "
           "sample of a longer text that should be detected as English because the "
           "and of to in for with that this are used more than twenty times in the "
           "following sentences that repeat the same words over and over again so the "
           "classifier has enough signal to pick the right language for this page "
           "content body.") * 3

ES_TEXT = ("El perro corre por el parque y la casa es grande y bonita pero el gato "
           "no quiere jugar con los niños en la calle de la ciudad que esta cerca del "
           "mar y las montañas y por eso la familia va de paseo cada fin de semana al "
           "campo para descansar del trabajo y de la rutina diaria de la vida en la "
           "gran ciudad moderna.") * 3

META_HTML = '''<html><head>
<meta property="og:title" content="Fotografía Alba">
<meta property="og:description" content="Fotógrafa de bodas en Valencia.">
<meta property="og:image" content="/img/alba.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="/fotografia-alba">
<meta name="author" content="Alba Ruiz">
<meta property="article:published_time" content="2026-01-15T10:00:00Z">
<link rel="icon" href="/favicon.png">
</head><body><h1>Fotografía Alba</h1></body></html>'''


def test_lang_from_html_attr():
    assert detect_language(LANG_ATTR_HTML, "") == "es"


def test_lang_from_content_language_meta():
    assert detect_language(CONTENT_LANG_HTML, "") == "fr"


def test_lang_from_name_language_meta():
    assert detect_language(NAME_LANG_HTML, "") == "de"


def test_lang_from_content_stopwords_en():
    assert detect_language("<html><body></body></html>", EN_TEXT) == "en"


def test_lang_from_content_stopwords_es():
    assert detect_language("", ES_TEXT) == "es"


def test_lang_empty_none():
    assert detect_language("", "") is None


def test_lang_short_text_none():
    assert detect_language("<html><body></body></html>", "hello world and stuff") is None


def test_extract_meta_fields():
    m = extract_meta(META_HTML, "https://example.com/post")
    assert m["ogTitle"] == "Fotografía Alba"
    assert m["ogDescription"] == "Fotógrafa de bodas en Valencia."
    assert m["twitterCard"] == "summary_large_image"
    assert m["author"] == "Alba Ruiz"
    assert m["publishedAt"] == "2026-01-15T10:00:00Z"


def test_extract_meta_relative_urls_absolute():
    m = extract_meta(META_HTML, "https://example.com/post")
    assert m["canonical"] == "https://example.com/fotografia-alba"
    assert m["ogImage"] == "https://example.com/img/alba.jpg"
    assert m["favicon"] == "https://example.com/favicon.png"


def test_extract_meta_empty_all_none():
    m = extract_meta("", "https://example.com")
    assert set(m.keys()) == {"canonical", "ogTitle", "ogDescription", "ogImage",
                             "twitterCard", "author", "publishedAt", "favicon"}
    assert all(v is None for v in m.values())


def test_extract_meta_broken_html_no_raise():
    m = extract_meta("<meta property=og:title content=unclosed", "https://example.com")
    assert m is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_meta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'growth_scraper.meta'`

- [ ] **Step 3: Add `LANG_STOPWORDS` to `config.py`**

Add after the existing `DEFAULT_*`/`SITEMAP_*` constants near the top of `src/growth_scraper/config.py`:

```python
LANG_STOPWORDS: dict[str, set[str]] = {
    "es": {"el", "la", "los", "las", "de", "y", "en", "es", "un", "una", "que", "para", "por", "con", "se", "del"},
    "en": {"the", "and", "of", "to", "in", "is", "a", "for", "with", "that", "this", "are", "as", "on"},
    "pt": {"o", "a", "os", "as", "de", "e", "em", "um", "uma", "que", "para", "por", "com", "se", "do", "da"},
    "fr": {"le", "la", "les", "de", "et", "en", "un", "une", "est", "pour", "par", "avec", "que", "des", "du"},
    "de": {"der", "die", "das", "und", "von", "in", "ist", "ein", "eine", "für", "mit", "zu", "den", "dem", "des"},
    "it": {"il", "la", "lo", "i", "gli", "le", "di", "e", "in", "un", "una", "per", "con", "che", "del", "della"},
}
```

- [ ] **Step 4: Write the minimal implementation**

`src/growth_scraper/meta.py`:

```python
"""Language detection + rich metadata extraction (pure HTML, no deps).

Fail-open by design: any error returns None / a dict with None values, never
raises. Consumed by the pipeline in run_one.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import LANG_STOPWORDS

_WORD_RE = re.compile(r"[a-zà-ü0-9']+")

META_KEYS = (
    "canonical", "ogTitle", "ogDescription", "ogImage",
    "twitterCard", "author", "publishedAt", "favicon",
)


def detect_language(html: str, text: str) -> str | None:
    """ISO 639-1 code, or None. Priority: <html lang> -> content-language
    meta -> stopword classification of `text` (needs enough signal)."""
    lang = _lang_from_attributes(html)
    if lang:
        return lang
    return _detect_by_content(text)


def _lang_from_attributes(html: str) -> str | None:
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        html_node = soup.find("html")
        if html_node and html_node.get("lang"):
            lang = str(html_node["lang"]).split("-")[0].strip().lower()
            if lang and len(lang) == 2:
                return lang
        for sel in ("meta[http-equiv='content-language']",
                    "meta[name='language']"):
            node = soup.select_one(sel)
            if node and node.get("content"):
                lang = str(node["content"]).split(",")[0].split("-")[0].strip().lower()
                if lang and len(lang) == 2:
                    return lang
    except Exception:
        pass
    return None


def _detect_by_content(text: str) -> str | None:
    tokens = _WORD_RE.findall(text.lower())
    if len(tokens) < 80:
        return None
    scores = {
        lang: sum(1 for t in tokens if t in sw)
        for lang, sw in LANG_STOPWORDS.items()
    }
    best_lang, best = max(scores.items(), key=lambda kv: kv[1])
    if best == 0:
        return None
    if best / len(tokens) < 0.05:
        return None
    rest = [v for l, v in scores.items() if l != best_lang]
    if rest and max(rest) and best / max(rest) < 1.5:
        return None
    return best_lang


def extract_meta(html: str, url: str) -> dict:
    """Stable-shape dict (all keys present, None when absent). Fail-open."""
    result = {k: None for k in META_KEYS}
    if not html:
        return result
    try:
        soup = BeautifulSoup(html, "html.parser")

        def value(sel: str) -> str | None:
            node = soup.select_one(sel)
            if node is None:
                return None
            raw = node.get("content") or node.get("href") or ""
            return " ".join(raw.strip().split()) or None

        result["canonical"] = _absolute(url, value("link[rel='canonical']"))
        result["ogTitle"] = value("meta[property='og:title']")
        result["ogDescription"] = value("meta[property='og:description']")
        result["ogImage"] = _absolute(url, value("meta[property='og:image']"))
        result["twitterCard"] = value("meta[name='twitter:card']")
        result["author"] = value("meta[name='author']") or value("meta[property='article:author']")
        result["publishedAt"] = value("meta[property='article:published_time']") or value("meta[name='date']")
        result["favicon"] = _absolute(url, value("link[rel~='icon']"))
    except Exception:
        pass
    return result


def _absolute(base: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urljoin(base, value)
    except Exception:
        return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_meta.py -q`
Expected: `10 passed`

- [ ] **Step 6: Commit**

```bash
git add src/growth_scraper/meta.py src/growth_scraper/config.py tests/test_meta.py
git commit -m "feat(fase3): meta.py — idioma (heurística) + metadata rica, con unit tests"
```

---

### Task 2: Wire config — `screenshot_dir`, `Record.meta`, `Record.screenshots`, `to_dict`

**Goal:** New config/record surface: `ScrapeConfig.screenshot_dir`, `Record.meta`, `Record.screenshots`, and unconditional serialization in `to_dict`.

**Files:**
- Modify: `src/growth_scraper/config.py`
- Test: `tests/test_meta.py` (append config wiring tests)

**Acceptance Criteria:**
- [ ] `ScrapeConfig.screenshot_dir: Optional[str] = None`
- [ ] `Record.meta: Optional[dict] = None`, `Record.screenshots: Optional[list] = None`
- [ ] `to_dict()` emits `meta` and `screenshots` unconditionally (None when unset), same as `structured`

**Verify:** `uv run pytest tests/test_meta.py -q` → all pass (Task 1 + 2 tests)

**Steps:**

- [ ] **Step 1: Write the failing test**

Append to `tests/test_meta.py`:

```python
def test_record_to_dict_meta_screenshots():
    from growth_scraper.config import Record

    r = Record(url="https://example.com")
    d = r.to_dict()
    assert d["meta"] is None
    assert d["screenshots"] is None

    r.meta = {"ogTitle": "X"}
    r.screenshots = ["/tmp/a.png"]
    d = r.to_dict()
    assert d["meta"] == {"ogTitle": "X"}
    assert d["screenshots"] == ["/tmp/a.png"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_meta.py::test_record_to_dict_meta_screenshots -q`
Expected: FAIL — `KeyError: 'meta'`

- [ ] **Step 3: Implement**

In `src/growth_scraper/config.py`:

Add to `ScrapeConfig` after the `export_images` line (line 166):

```python
    screenshot_dir: Optional[str] = None
```

Add to `Record` after the `structured` line (line 209):

```python
    meta: Optional[dict] = None
    screenshots: Optional[list] = None
```

Add to `Record.to_dict()` after the `"structured": self.structured,` line (line 227):

```python
            "meta": self.meta,
            "screenshots": self.screenshots,
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_meta.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/growth_scraper/config.py tests/test_meta.py
git commit -m "feat(fase3): config — screenshot_dir, Record.meta/screenshots, to_dict"
```

---

### Task 3: `screenshot.py` + pipeline integration + e2e tests

**Goal:** The pipeline populates `record.meta`, `summary.language`, and `record.screenshots`; screenshots are captured in the `before_retrieve_html` hook and propagated only on success.

**Files:**
- Create: `src/growth_scraper/screenshot.py`
- Modify: `src/growth_scraper/pipeline.py`
- Modify: `tests/fixtureserver.py` (add `/meta-rich` route)
- Test: `tests/test_meta.py` (append e2e tests)

**Acceptance Criteria:**
- [ ] `capture_screenshot(page, url, out_dir)` writes a full-page PNG named `{host}-{ts}-{digest}.png` and returns its absolute path; any error → `None`
- [ ] `Session.screenshot_path` defaults to `None`; the hook sets it when `cfg.screenshot_dir` is set and the page is alive
- [ ] `run_one`: `record.meta` from `extract_meta(raw_html, url)`; `summary.language` set when detected; `record.screenshots = [path]` on success when captured
- [ ] Fixture `/meta-rich` serves `<html lang="es">` + OG/canonical/author/published_time/favicon
- [ ] Server mode unchanged; records from `/scrape` carry `meta`/`screenshots` as `null` (defaults)

**Verify:** `uv run pytest tests/test_meta.py -q` → all pass (13 tests) and `uv run pytest tests/ -q` → previous tests still green

**Steps:**

- [ ] **Step 1: Write the failing e2e tests**

Append to `tests/test_meta.py`:

```python
def test_e2e_meta_rich_through_browser(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(), fs.url("/meta-rich")))
    assert rec.meta["ogTitle"] == "Fotografía Alba"
    assert rec.meta["canonical"] == fs.url("/fotografia-alba")
    assert rec.meta["publishedAt"] == "2026-01-15T10:00:00Z"
    assert rec.summary["language"] == "es"


def test_e2e_screenshot_through_browser(fs, tmp_path):
    import os

    from conftest import base_cfg, run, scrape_url

    cfg = base_cfg(screenshot_dir=str(tmp_path))
    rec = run(scrape_url(cfg, fs.url("/meta-rich")))
    assert rec.screenshots and len(rec.screenshots) == 1
    p = rec.screenshots[0]
    assert os.path.exists(p)
    with open(p, "rb") as f:
        assert f.read(4) == b"\x89PNG"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_meta.py -q`
Expected: FAIL — `/meta-rich` returns 404 and `rec.meta` is `None`

- [ ] **Step 3: Add the `/meta-rich` fixture route**

In `tests/fixtureserver.py` `build_pages()`, add after the `/structured-none` entry (last item):

```python
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
```

Also update the module docstring route list to include `  /meta-rich    meta/language-rich page (Fase 3)`.

- [ ] **Step 4: Write `screenshot.py`**

`src/growth_scraper/screenshot.py`:

```python
"""Full-page screenshot capture (CLI-only multimodal pointer).

CLI writes PNGs to a directory and the record carries the paths; the server
never writes to disk (same rationale as --export-images).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from urllib.parse import urlparse


async def capture_screenshot(page, url: str, out_dir: str) -> str | None:
    """Save a full-page PNG into out_dir. Returns the absolute path or None."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        host = (urlparse(url).hostname or "unknown").replace(".", "_")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha1(url.encode()).hexdigest()[:8]
        path = os.path.join(out_dir, f"{host}-{ts}-{digest}.png")
        await page.screenshot(path=path, full_page=True)
        return os.path.abspath(path)
    except Exception:
        return None
```

- [ ] **Step 5: Wire the pipeline**

In `src/growth_scraper/pipeline.py`:

Add imports after `from .structured import extract_structured` (line 16):

```python
from .meta import detect_language, extract_meta
from .screenshot import capture_screenshot
```

Add `screenshot_path` to `Session.__init__` after `self.cfg` (line 40):

```python
        self.screenshot_path: str | None = None
```

Extend `_hook_before_retrieve_html` — change the signature to accept `url` and add the capture block before the `return page` (lines 241-257):

```python
    async def _hook_before_retrieve_html(self, page, context, url="", config=None, **kwargs):
        # P1: the page is alive here and has already fired its XHRs; replay any
        # paginated internal APIs so we capture more than the default load.
        session = self._session_for(config)
        cfg = session.cfg or self.cfg
        if cfg.capture_apis and cfg.expand and session.page_url:
            try:
                session.netrec.deactivate()  # replay fetches must not be re-captured
                captured = session.netrec.snapshot()
                session.replayed = await apipage.replay_pagination(
                    page, session.page_url, captured
                )
                if session.replayed:
                    emit_progress(cfg.verbose, f"pagination replay: +{len(session.replayed)} responses")
            except Exception as exc:
                emit_progress(cfg.verbose, f"pagination replay failed: {exc}")
        # Fase 3: full-page screenshot (CLI-only) while the page is alive+expanded
        if cfg.screenshot_dir and session.page:
            session.screenshot_path = await capture_screenshot(
                session.page, url or session.page_url, cfg.screenshot_dir
            )
            if session.screenshot_path:
                emit_progress(cfg.verbose, f"screenshot: {session.screenshot_path}")
        return page
```

In `run_one`, after `record.summary = _build_summary(...)` (line 373), add:

```python
        # Fase 3: language triage + rich metadata
        lang = detect_language(raw_html, record.text)
        if lang:
            record.summary["language"] = lang
        record.meta = extract_meta(raw_html, url)
```

In `run_one`, after the `--export-images` block (line 391), add:

```python
        if used_session is not None and used_session.screenshot_path:
            record.screenshots = [used_session.screenshot_path]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_meta.py -q`
Expected: `13 passed`

Run: `uv run pytest tests/ -q`
Expected: all pass (previous tests green — note: `test_scraper.py` and `test_server.py` count stays stable because `meta`/`screenshots` are additive)

- [ ] **Step 7: Commit**

```bash
git add src/growth_scraper/screenshot.py src/growth_scraper/pipeline.py tests/fixtureserver.py tests/test_meta.py
git commit -m "feat(fase3): screenshots + pipeline wiring — record.meta, summary.language, record.screenshots"
```

---

### Task 4: CLI `--screenshots DIR` + CSV `language` column + tests

**Goal:** Users can enable screenshots via the CLI, and the CSV mirror exposes the detected language.

**Files:**
- Modify: `src/growth_scraper/cli.py`
- Modify: `src/growth_scraper/records.py`
- Test: `tests/test_meta.py` (append CLI + CSV tests)

**Acceptance Criteria:**
- [ ] `--screenshots DIR` flag maps to `ScrapeConfig.screenshot_dir`
- [ ] `CsvWriter` header and rows include a `language` column (from `summary.language`)
- [ ] CLI run with `--screenshots DIR` writes one PNG per URL and the JSONL contains the path
- [ ] CSV row for `/meta-rich` has `language` == `es`

**Verify:** `uv run pytest tests/test_meta.py -q` → all pass (15 tests)

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_meta.py`:

```python
def test_cli_screenshots_flag(fs, tmp_path):
    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    shots = tmp_path / "shots"
    code = main([fs.url("/meta-rich"), "-o", str(out), "--screenshots", str(shots),
                 "--delay", "0", "--jitter", "0"])
    assert code == 0
    pngs = list(shots.glob("*.png"))
    assert len(pngs) == 1
    line = out.read_text().splitlines()[0]
    assert str(pngs[0]) in line


def test_csv_language_column(fs, tmp_path):
    import csv

    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    code = main([fs.url("/meta-rich"), "-o", str(out), "--csv",
                 "--delay", "0", "--jitter", "0"])
    assert code == 0
    with open(tmp_path / "cli.csv", "r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    assert "language" in header
    idx = header.index("language")
    assert rows[1][idx] == "es"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_meta.py::test_cli_screenshots_flag tests/test_meta.py::test_csv_language_column -q`
Expected: FAIL — `unrecognized arguments: --screenshots` and `KeyError: 'language'`

- [ ] **Step 3: Add the CLI flag**

In `src/growth_scraper/cli.py`, add after the `--export-images` argument (line 57):

```python
    p.add_argument("--screenshots", metavar="DIR", help="Save a full-page PNG per URL into DIR (Fase 3 multimodal pointer).")
```

In `main`, add to the `ScrapeConfig(...)` call after `export_images=args.export_images,` (line 109):

```python
        screenshot_dir=args.screenshots,
```

- [ ] **Step 4: Add the CSV column**

In `src/growth_scraper/records.py` `CsvWriter.HEADERS`, append `"language"` at the end of the list (line 49):

```python
        "structuredReviewCount", "structuredCategory", "language",
```

In `CsvWriter.write`, append `s.get("language", ""),` to the row list after `s.get("structuredCategory", ""),` (line 76):

```python
            s.get("structuredCategory", ""),
            s.get("language", ""),
        ])
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_meta.py -q`
Expected: `15 passed`

Run: `uv run pytest tests/ -q`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add src/growth_scraper/cli.py src/growth_scraper/records.py tests/test_meta.py
git commit -m "feat(fase3): CLI --screenshots + columna language en CSV"
```

---

### Task 5: Docs — README output enriquecido + AGENTS (módulos, D30–D33, evidencia)

**Goal:** Document the new output surface and record the Fase 3 decisions/evidence in the project docs.

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Acceptance Criteria:**
- [ ] README documents `meta` fields, `summary.language`, and `--screenshots DIR`
- [ ] AGENTS adds module rows for `meta.py`/`screenshot.py`, decisions D30–D33, and test evidence
- [ ] No placeholder text; matches existing doc style

**Verify:** `uv run pytest tests/ -q` → all green (unchanged); git diff reviewed

**Steps:**

- [ ] **Step 1: README — output enriquecido**

In `README.md`, after the existing structured-output section, add:

```markdown
### Output enriquecido (Fase 3)

Cada record añade tres cosas sobre el output base:

- **`meta`** — shape estable (keys con `null` si ausentes): `canonical`,
  `ogTitle`, `ogDescription`, `ogImage`, `twitterCard`, `author`, `publishedAt`,
  `favicon`. Las URLs relativas se resuelven contra la URL de la página.
- **`summary.language`** — código ISO 639-1 detectado con heurística sin
  dependencias (`<html lang>` → `content-language` → stopwords). Solo aparece
  cuando se detecta con confianza.
- **`screenshots`** — rutas a PNG full-page capturados con `--screenshots DIR`
  (puntero multimodal, igual que `--export-images`). Solo CLI: el server mode
  nunca escribe a disco.

Ejemplo:

```json
{"url": "https://ejemplo.com", "meta": {"ogTitle": "…", "language"…}}
```

La columna `language` también aparece en el CSV cuando usas `--csv`.
```

- [ ] **Step 2: AGENTS — módulos, decisiones y evidencia**

In `AGENTS.md`, in the modules table, add rows (matching the existing row style):

```markdown
| `meta.py` | Idioma (ISO 639-1, heurística sin deps, fail-open) + metadata rica (`meta`: canonical, OG, twitter, author, publishedAt, favicon) |
| `screenshot.py` | Screenshot full-page CLI-only (`--screenshots DIR`), campo `screenshots` con rutas |
```

In the decisions section, add:

```markdown
- **D30** — Idioma fail-open sin dependencias: `<html lang>` → `content-language` → stopwords (texto ≥80 palabras, ratio ≥0.05, margen 1.5×). Sin señal → `None`.
- **D31** — `meta` es un campo de shape estable (keys con `null`), separado del triage de `summary` (solo `language` ahí y en CSV).
- **D32** — Screenshots solo CLI (`--screenshots DIR`), PNG full-page en disco, campo puntero `screenshots`; el server nunca escribe a disco.
- **D33** — Captura en `before_retrieve_html` (página viva y expandida); `session.screenshot_path` → `record.screenshots` solo en éxito.
```

Add to the test-evidence section:

```markdown
- Fase 3 (output enriquecido): `uv run pytest tests/test_meta.py -q` → 15 passed (unit idioma/metadata, e2e `/meta-rich`, screenshot PNG, CSV `language`, CLI `--screenshots`).
```

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs(fase3): output enriquecido — meta, summary.language, screenshots (D30-D33)"
```

---

## Full-suite verification (after Task 5)

- [ ] `uv run pytest tests/ -q` → all green (47 previous + 15 new)
- [ ] `bash scripts/fixture-check.sh` → PASS
- [ ] If internet: `bash scripts/scenario-server.sh` → PASS (server regression)
- [ ] Smoke: `uv run gscrape https://www.python.org -o /tmp/py.jsonl --screenshots /tmp/shots --delay 0` → record has `meta`, `summary.language == "en"`, one PNG in `/tmp/shots`
