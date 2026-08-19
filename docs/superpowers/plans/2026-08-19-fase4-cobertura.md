# Fase 4: Cobertura (consent walls SPA, iframes cross-origin, anti-bot intermitente) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three coverage gaps: wait (bounded) for gated content after consent dismissal, report + fetch cross-origin iframes, and retry 403/429 with a hard cap plus evidence.

**Architecture:** Two new focused modules — `waitcontent.py` (bounded settle-wait after a consent action) and `iframes.py` (pure iframe inventory + polite GET of each `src`) — plus a bounded anti-bot retry path inside `run_one`'s existing retry loop. New config knobs and record fields (`frames`, `frameTexts`, `retries`) stay additive; the `orpheus.sh` contract is untouched.

**Tech Stack:** Python 3 (stdlib + `bs4` + `httpx` — already dependencies), Playwright via crawl4ai, pytest, uv.

---

### Task 1: Config — Fase 4 knobs + record fields + to_dict

**Goal:** New `ScrapeConfig` knobs, `Record` fields and unconditional `to_dict` serialization for the Fase 4 surface.

**Files:**
- Modify: `src/growth_scraper/config.py`
- Test: `tests/test_coverage.py` (new file, config wiring tests)

**Acceptance Criteria:**
- [ ] `ScrapeConfig`: `consent_wait_ms: int = 5000`, `fetch_frames: bool = True`, `max_frames: int = 5`, `anti_bot_retries: int = 2`, `anti_bot_backoff_s: float = 15.0`
- [ ] `Record`: `frames: Optional[list] = None`, `frameTexts: Optional[list] = None`, `retries: int = 0`
- [ ] `to_dict()` emits `frames`, `frameTexts`, `retries` unconditionally
- [ ] Existing tests still green

**Verify:** `uv run pytest tests/test_coverage.py -q` → 2 passed

**Steps:**

- [ ] **Step 1: Write the failing tests**

`tests/test_coverage.py`:

```python
"""Fase 4 coverage tests: consent gating, cross-origin iframes, anti-bot retry."""


def test_record_to_dict_coverage_fields():
    from growth_scraper.config import Record

    r = Record(url="https://example.com")
    d = r.to_dict()
    assert d["frames"] is None
    assert d["frameTexts"] is None
    assert d["retries"] == 0

    r.frames = [{"src": "https://x.com/f", "crossOrigin": True}]
    r.frameTexts = [{"src": "https://x.com/f", "text": "hola"}]
    r.retries = 2
    d = r.to_dict()
    assert d["frames"][0]["crossOrigin"] is True
    assert d["frameTexts"][0]["text"] == "hola"
    assert d["retries"] == 2


def test_scrape_config_fase4_defaults():
    from growth_scraper.config import ScrapeConfig

    c = ScrapeConfig()
    assert c.consent_wait_ms == 5000
    assert c.fetch_frames is True
    assert c.max_frames == 5
    assert c.anti_bot_retries == 2
    assert c.anti_bot_backoff_s == 15.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: FAIL — `KeyError: 'frames'` / `AttributeError: consent_wait_ms`

- [ ] **Step 3: Implement**

In `src/growth_scraper/config.py`:

Add to `ScrapeConfig` after the `screenshot_dir` line:

```python
    consent_wait_ms: int = 5000
    fetch_frames: bool = True
    max_frames: int = 5
    anti_bot_retries: int = 2
    anti_bot_backoff_s: float = 15.0
```

Add to `Record` after the `screenshots` line:

```python
    frames: Optional[list] = None
    frameTexts: Optional[list] = None
    retries: int = 0
```

Add to `Record.to_dict()` after the `"screenshots": self.screenshots,` line:

```python
            "frames": self.frames,
            "frameTexts": self.frameTexts,
            "retries": self.retries,
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/growth_scraper/config.py tests/test_coverage.py
git commit -m "feat(fase4): config — consent_wait_ms, fetch_frames, anti_bot_retries, Record.frames/frameTexts/retries"
```

---

### Task 2: `waitcontent.py` + consent hook wiring + e2e `/gated`

**Goal:** After a consent action (rejected/hidden/removed), wait bounded for gated content to load so extraction sees the complete page.

**Files:**
- Create: `src/growth_scraper/waitcontent.py`
- Modify: `src/growth_scraper/pipeline.py` (`_hook_after_goto`)
- Modify: `tests/fixtureserver.py` (add `/gated` route)
- Test: `tests/test_coverage.py` (append e2e)

**Acceptance Criteria:**
- [ ] `wait_for_content(page, cfg)` polls `document.body.innerText.length` every ~500ms, stops early on plateau (Δ < 5% over 2 polls), hard cap `cfg.consent_wait_ms`, never raises, returns a short summary string
- [ ] Hook: called only when `handle_consent` did something (`rejected*`/`hidden*`/`removed*`); skipped on `no-consent-found`/`error`/disabled
- [ ] e2e `/gated`: with `consent_wait_ms=3000` the record text contains the late-loaded content

**Verify:** `uv run pytest tests/test_coverage.py -q` → 3 passed

**Steps:**

- [ ] **Step 1: Write the failing e2e test**

Append to `tests/test_coverage.py`:

```python
def test_e2e_gated_content_with_wait(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(consent_wait_ms=3000), fs.url("/gated")))
    assert "Contenido adicional que carga tras rechazar" in rec.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_coverage.py::test_e2e_gated_content_with_wait -q`
Expected: FAIL — `/gated` 404s (route missing) and text lacks the content

- [ ] **Step 3: Add the `/gated` fixture route**

In `tests/fixtureserver.py` `build_pages()`, add this as the LAST entry:

```python
        "/gated": """<!doctype html><html><head><meta charset="utf-8"><title>Página con consent</title></head>
<body>
<div id="onetrust-consent-sdk">
  <div class="onetrust-banner">
    <button id="onetrust-reject-all-handler">Reject</button>
    <button id="onetrust-accept-btn-handler">Accept</button>
  </div>
</div>
<h1>Página con consent</h1>
<p>Contenido inicial visible de la página con consentimiento.</p>
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
```

Also update the module docstring route list with:
`  /gated       consent wall that gates content until rejected (Fase 4)`

- [ ] **Step 4: Create `waitcontent.py`**

`src/growth_scraper/waitcontent.py`:

```python
"""Bounded wait for gated content after a consent dismissal.

Some SPAs only load real content into the DOM once the consent wall is
dismissed. After a reject/hide action we wait (bounded) for the body text to
stabilize so extraction sees the complete page. Fail-open: never raises.
"""

from __future__ import annotations

import asyncio
import time

_POLL_MS = 500
_PLATEAU_RATIO = 0.05
_PLATEAU_POLLS = 2


async def _text_len(page) -> int:
    try:
        return int(await page.evaluate("document.body.innerText.length"))
    except Exception:
        return -1


async def wait_for_content(page, cfg) -> str:
    """Wait until body text stops growing, capped at cfg.consent_wait_ms."""
    if not cfg.consent_wait_ms:
        return "off"
    start = time.monotonic()
    prev = await _text_len(page)
    stable = 0
    while time.monotonic() - start < cfg.consent_wait_ms / 1000:
        await asyncio.sleep(_POLL_MS / 1000)
        cur = await _text_len(page)
        if prev >= 0 and cur >= 0:
            growth = (cur - prev) / max(prev, 1)
            if growth < _PLATEAU_RATIO:
                stable += 1
                if stable >= _PLATEAU_POLLS:
                    return f"wait {time.monotonic() - start:.1f}s delta {cur - prev:+d}"
            else:
                stable = 0
        prev = cur
    return f"wait timeout {cfg.consent_wait_ms}ms len {prev}"


def needs_wait(consent_status: str) -> bool:
    """True only when the consent handler actually did something."""
    if not consent_status:
        return False
    if consent_status.startswith(("no-consent", "error")):
        return False
    return True
```

- [ ] **Step 5: Wire the hook in `pipeline.py`**

Add import after `from .screenshot import capture_screenshot`:

```python
from .waitcontent import needs_wait, wait_for_content
```

In `_hook_after_goto`, change the consent block from:

```python
            if cfg.handle_consent:
                status = await handle_consent(page)
                emit_progress(cfg.verbose, f"consent on {url}: {status}")
```

to:

```python
            if cfg.handle_consent:
                status = await handle_consent(page)
                emit_progress(cfg.verbose, f"consent on {url}: {status}")
                if needs_wait(status):
                    waited = await wait_for_content(page, cfg)
                    emit_progress(cfg.verbose, f"wait_for_content on {url}: {waited}")
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add src/growth_scraper/waitcontent.py src/growth_scraper/pipeline.py tests/fixtureserver.py tests/test_coverage.py
git commit -m "feat(fase4): waitcontent — espera acotada tras dismiss del consent (contenido gated)"
```

---

### Task 3: `iframes.py` — extract + fetch frame srcs + wiring + e2e `/frames`

**Goal:** Report iframes (`frames`) and capture text of each `src` via polite GET (`frameTexts`).

**Files:**
- Create: `src/growth_scraper/iframes.py`
- Modify: `src/growth_scraper/pipeline.py` (`run_one`)
- Modify: `tests/fixtureserver.py` (add `/frames`, `/frame-content` routes)
- Test: `tests/test_coverage.py` (append unit + e2e)

**Acceptance Criteria:**
- [ ] `extract_iframes(url, html, max_frames)` returns `[{src, title, crossOrigin}]`, skips empty/`data:`/`about:`/`blob:` srcs, resolves relative srcs via `urljoin`, caps at `max_frames`, fail-open
- [ ] `fetch_frame_texts(srcs, cfg, robots, limit=2000)` does a bounded-concurrency (max 3) polite GET per unique src, skips robots-disallowed, returns `[{src, text}]` with text truncated to 2000 chars, fail-open per frame
- [ ] `run_one`: `record.frames` from raw HTML; `record.frameTexts` when `cfg.fetch_frames`
- [ ] e2e `/frames`: `frames` has 2 entries (one `crossOrigin` True via `localhost`, one False), `frameTexts` includes the cross-origin text

**Verify:** `uv run pytest tests/test_coverage.py -q` → 6 passed

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coverage.py`:

```python
def test_extract_iframes_pure():
    from growth_scraper.iframes import extract_iframes

    html = """<iframe src="/a" title="A"></iframe>
    <iframe src="https://ext.example.com/b"></iframe>
    <iframe src="about:blank"></iframe>
    <iframe src="data:text/html,hi"></iframe>
    <iframe></iframe>"""
    frames = extract_iframes("https://site.example.com/pag", html, max_frames=10)
    assert len(frames) == 2
    assert frames[0]["src"] == "https://site.example.com/a"
    assert frames[0]["crossOrigin"] is False
    assert frames[1]["src"] == "https://ext.example.com/b"
    assert frames[1]["crossOrigin"] is True


def test_extract_iframes_cap_and_empty():
    from growth_scraper.iframes import extract_iframes

    html = "<iframe src='/x1'></iframe><iframe src='/x2'></iframe><iframe src='/x3'></iframe>"
    assert len(extract_iframes("https://s.example.com", html, max_frames=2)) == 2
    assert extract_iframes("https://s.example.com", "", max_frames=5) == []


def test_e2e_frames_through_browser(fs):
    from conftest import base_cfg, run, scrape_url

    rec = run(scrape_url(base_cfg(), fs.url("/frames")))
    assert rec.frames and len(rec.frames) == 2
    cross = [f for f in rec.frames if f["crossOrigin"]]
    assert len(cross) == 1
    assert rec.frameTexts and any("frame externo" in ft["text"] for ft in rec.frameTexts)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: FAIL — `ModuleNotFoundError: growth_scraper.iframes` and `/frames` 404

- [ ] **Step 3: Add the `/frames` + `/frame-content` fixture routes**

In `tests/fixtureserver.py`, in `do_GET`, add before the generic `html = self.pages.get(path)` block:

```python
        if path == "/frames":
            # cross-origin via the "localhost" hostname hitting the same server
            port = self.headers["Host"].rsplit(":", 1)[-1]
            body = (f"""<h1>Página con iframes</h1>
<iframe src="http://localhost:{port}/frame-content" title="frame externo"></iframe>
<iframe src="/local-frame"></iframe>
<iframe src="about:blank"></iframe>""").encode()
            self._send(200, body)
            return
        if path == "/frame-content":
            self._send(200, b"<html><body><p>Contenido del frame externo que hay que capturar.</p></body></html>")
            return
        if path == "/local-frame":
            self._send(200, b"<html><body><p>Contenido del frame local.</p></body></html>")
            return
```

Also update the module docstring route list with:
`  /frames      page with cross-origin + same-origin iframes (Fase 4)`

- [ ] **Step 4: Create `iframes.py`**

`src/growth_scraper/iframes.py`:

```python
"""Cross-origin iframe coverage: inventory + polite GET of each src.

Reports the iframes of a page (`frames`) and captures the visible text of each
fetched src (`frameTexts`) via a plain GET — same-origin policy is not touched.
Polite: robots-respecting, bounded concurrency, per-frame timeout, fail-open.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

_FRAME_LIMIT_CHARS = 2000


def extract_iframes(url: str, html: str, max_frames: int = 5) -> list[dict]:
    """Inventory of <iframe> elements: src (absolute), title, crossOrigin."""
    if not html:
        return []
    frames: list[dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        page_host = (urlparse(url).hostname or "").lower()
        for node in soup.find_all("iframe"):
            if len(frames) >= max_frames:
                break
            src = (node.get("src") or "").strip()
            if not src or src.startswith(("data:", "about:", "blob:")):
                continue
            abs_src = urljoin(url, src)
            host = (urlparse(abs_src).hostname or "").lower()
            frames.append({
                "src": abs_src,
                "title": (node.get("title") or "").strip() or None,
                "crossOrigin": bool(host and host != page_host),
            })
    except Exception:
        pass
    return frames


async def fetch_frame_texts(srcs: list[str], cfg, robots, limit: int = _FRAME_LIMIT_CHARS) -> list[dict]:
    """Polite GET per unique iframe src. Returns [{src, text}] for successes."""
    import httpx

    unique = list(dict.fromkeys(srcs))
    results: list[dict] = []
    sem = asyncio.Semaphore(3)

    async def fetch_one(src: str) -> None:
        try:
            if not await robots.is_allowed(src):
                return
            async with sem:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(src)
                    if resp.status_code != 200:
                        return
                    soup = BeautifulSoup(resp.text, "html.parser")
                    text = " ".join(soup.get_text(" ", strip=True).split())
                    if text:
                        results.append({"src": src, "text": text[:limit]})
        except Exception:
            return

    await asyncio.gather(*(fetch_one(s) for s in unique))
    return results
```

- [ ] **Step 5: Wire `run_one` in `pipeline.py`**

Add import after `from .waitcontent import needs_wait, wait_for_content`:

```python
from .iframes import extract_iframes, fetch_frame_texts
```

In `run_one`, after the screenshots block (after `record.screenshots = [used_session.screenshot_path]`), add:

```python
        # Fase 4: iframe inventory + polite fetch of each src
        if raw_html:
            record.frames = extract_iframes(url, raw_html, cfg.max_frames) or None
            if cfg.fetch_frames and record.frames:
                record.frameTexts = await fetch_frame_texts(
                    [f["src"] for f in record.frames], cfg, self.robots
                ) or None
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: `6 passed`

Run: `uv run pytest tests/ -q` (allow ~10 min)
Expected: all green (63 previous + new)

- [ ] **Step 7: Commit**

```bash
git add src/growth_scraper/iframes.py src/growth_scraper/pipeline.py tests/fixtureserver.py tests/test_coverage.py
git commit -m "feat(fase4): iframes — inventario frames + fetch polite del src (frameTexts)"
```

---

### Task 4: Anti-bot retry 403/429 con tope + CSV `retries` + CLI flags + e2e

**Goal:** Retry 403/429 with a hard cap and long backoff, record evidence, expose the CSV column and CLI knobs.

**Files:**
- Modify: `src/growth_scraper/pipeline.py` (`run_one` retry loop)
- Modify: `src/growth_scraper/records.py` (CSV `retries`)
- Modify: `src/growth_scraper/cli.py` (`--no-frames`, `--consent-wait-ms`, `--anti-bot-retries`, `--anti-bot-backoff`)
- Modify: `tests/fixtureserver.py` (add `/flaky403` route)
- Test: `tests/test_coverage.py` (append e2e)

**Acceptance Criteria:**
- [ ] Retry loop: `attempts = 1 + max_retries + anti_bot_retries`; retries on `status >= 500` (existing backoff) AND on `status in (403, 429)` (backoff `anti_bot_backoff_s` + jitter); `record.retries` = actual re-attempts
- [ ] robots disallowed still returns immediately (no retry)
- [ ] CSV header+row include `retries`
- [ ] CLI: `--no-frames`, `--consent-wait-ms N`, `--anti-bot-retries N`, `--anti-bot-backoff N` wired
- [ ] e2e `/flaky403?fails=2`: with retries succeeds, `retries == 2`; with retries 0 (`?fails=999`) ends `protectionBlocked`

**Verify:** `uv run pytest tests/test_coverage.py -q` → 8 passed

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coverage.py`:

```python
def test_e2e_flaky403_retry_succeeds(fs):
    from conftest import base_cfg, run, scrape_url

    cfg = base_cfg(max_retries=0, anti_bot_retries=2, anti_bot_backoff_s=0.01)
    rec = run(scrape_url(cfg, fs.url("/flaky403?fails=2")))
    assert rec.retries == 2
    assert not rec.error
    assert not rec.protectionBlocked


def test_e2e_flaky403_no_retry_blocked(fs):
    from conftest import base_cfg, run, scrape_url

    cfg = base_cfg(max_retries=0, anti_bot_retries=0)
    rec = run(scrape_url(cfg, fs.url("/flaky403?fails=999")))
    assert rec.protectionBlocked is True
    assert rec.retries == 0


def test_csv_retries_column(fs, tmp_path):
    import csv

    from growth_scraper.cli import main

    out = tmp_path / "cli.jsonl"
    code = main([fs.url("/"), "-o", str(out), "--csv",
                 "--delay", "0", "--jitter", "0"])
    assert code == 0
    with open(tmp_path / "cli.csv", "r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert "retries" in rows[0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_coverage.py::test_e2e_flaky403_retry_succeeds tests/test_coverage.py::test_e2e_flaky403_no_retry_blocked tests/test_coverage.py::test_csv_retries_column -q`
Expected: FAIL — 403 not retried (record blocked on first hit), CSV lacks `retries`

- [ ] **Step 3: Add the `/flaky403` fixture route**

In `tests/fixtureserver.py`, in `do_GET`, add before the generic block:

```python
        if path == "/flaky403":
            key = self.path  # unique per ?fails=N, so tests are order-independent
            self.state.hits[key] += 1
            fails = int(urlparse(self.path).query.split("=")[1]) if "=" in urlparse(self.path).query else 0
            if self.state.hits[key] <= fails:
                self._send(403, b"<html><body>challenge</body></html>", headers={"Server": "cloudflare"})
                return
            html = self.pages.get("/") or ""
            self._send(200, html.encode("utf-8"))
            return
```

Also update the module docstring route list with:
`  /flaky403   intermittent 403 (anti-bot) that settles after N fails (Fase 4)`

- [ ] **Step 4: Extend the retry loop in `pipeline.py`**

In `run_one`, replace the current retry-loop setup and 5xx branch:

```python
        attempts = 1 + cfg.max_retries + cfg.anti_bot_retries
        result = None
        last_exc: Exception | None = None
        used_session: Session | None = None
        retries_done = 0
        for attempt in range(attempts):
            sid = uuid.uuid4().hex
            session = Session()
            session.page_url = url
            session.cfg = cfg
            session.netrec.reset(url)  # starts the recorder active for this page
            self._sessions[sid] = session
            run_cfg = copy.copy(self.run_cfg)
            run_cfg.session_id = sid
            try:
                result = await self.crawler.arun(url, config=run_cfg)
                if result is None:
                    raise RuntimeError("crawl4ai returned no result")
                status = getattr(result, "status_code", None)
                if status is not None and status >= 500 and attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: status {status} (attempt {attempt + 1}/{attempts})")
                    result = None
                    retries_done += 1
                    continue
                if status in (403, 429) and attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: status {status} (anti-bot, attempt {attempt + 1}/{attempts})")
                    result = None
                    retries_done += 1
                    await asyncio.sleep(cfg.anti_bot_backoff_s + random.uniform(0, cfg.jitter))
                    continue
                used_session = session
                break
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    emit_progress(cfg.verbose, f"retry {url}: {exc}")
                    await asyncio.sleep(cfg.retry_backoff * (2 ** attempt))
                    continue
                break
            finally:
                self._sessions.pop(sid, None)
                try:
                    await self.crawler.crawler_strategy.kill_session(sid)
                except Exception:
                    pass

        if retries_done:
            record.retries = retries_done
```

Ensure `import random` is present at the top of `pipeline.py` (it is used elsewhere? if not, add `import random`).

- [ ] **Step 5: CSV column**

In `src/growth_scraper/records.py`:

(a) In `CsvWriter.HEADERS`, append `"retries"` at the end:

```python
        "structuredReviewCount", "structuredCategory", "language", "retries",
```

(b) In `CsvWriter.write`, append `record.retries or ""` at the end of the row list:

```python
            s.get("language", ""),
            record.retries or "",
        ])
```

- [ ] **Step 6: CLI flags**

In `src/growth_scraper/cli.py`:

Add after the `--screenshots` argument:

```python
    p.add_argument("--no-frames", action="store_true", help="Skip iframe detection and frame-text fetch.")
    p.add_argument("--consent-wait-ms", type=int, default=5000, help="Max ms to wait for gated content after consent dismissal (0 = off).")
    p.add_argument("--anti-bot-retries", type=int, default=2, help="Extra retries on 403/429 with long backoff (0 = permanent block).")
    p.add_argument("--anti-bot-backoff", type=float, default=15.0, help="Base seconds between anti-bot retries.")
```

In `main`, add to the `ScrapeConfig(...)` call after `screenshot_dir=args.screenshots,`:

```python
        consent_wait_ms=args.consent_wait_ms,
        fetch_frames=not args.no_frames,
        anti_bot_retries=max(0, args.anti_bot_retries),
        anti_bot_backoff_s=args.anti_bot_backoff,
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: `8 passed`

Run: `uv run pytest tests/ -q` (allow ~10 min)
Expected: all green (63 previous + new)

- [ ] **Step 8: Commit**

```bash
git add src/growth_scraper/pipeline.py src/growth_scraper/records.py src/growth_scraper/cli.py tests/fixtureserver.py tests/test_coverage.py
git commit -m "feat(fase4): anti-bot intermitente — retry 403/429 con tope, CSV retries, flags CLI"
```

---

### Task 5: Docs — README + AGENTS (D34–D36, evidencia)

**Goal:** Document Fase 4 output and record the decisions/evidence.

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Acceptance Criteria:**
- [ ] README documents gated-content wait, `frames`/`frameTexts`, and anti-bot retry (`retries`)
- [ ] AGENTS adds module rows (`waitcontent.py`, `iframes.py`), decisions D34–D36, test evidence
- [ ] No placeholders; matches existing style

**Verify:** `git show --stat HEAD` → only 2 files

**Steps:**

- [ ] **Step 1: README**

After the "Output enriquecido (Fase 3)" section, add:

```markdown
### Cobertura (Fase 4)

Tres mejoras de cobertura, todas aditivas sobre el record:

- **Contenido gated tras consent** — si al descartar el consent el contenido
  tarda en aparecer, esperamos (acotado, `--consent-wait-ms`) a que se
  estabilice para que el texto salga completo. Solo ocurre cuando el handler de
  consent hizo algo.
- **`frames` / `frameTexts`** — inventario de iframes (`src`, `title`,
  `crossOrigin`) y texto del `src` de cada iframe vía GET polite (robots +
  timeout + cap). Se desactiva con `--no-frames`.
- **`retries`** — el campo del record indica cuántos reintentos hubo. Ante
  403/429 intermitentes se reintenta con tope y backoff largo
  (`--anti-bot-retries`, `--anti-bot-backoff`); `robots.txt` disallowed nunca
  reintenta. No hay evasión: solo resiliencia acotada y registrada.
```

- [ ] **Step 2: AGENTS**

(a) In the modules table, add rows:

```markdown
| `waitcontent.py` | Espera acotada (polling innerText, plateau) tras dismiss del consent para contenido gated |
| `iframes.py` | Inventario de iframes (`frames`: src/title/crossOrigin) + fetch polite del src (`frameTexts`) |
```

(b) In the decisions section, add:

```markdown
- **D34** — Contenido gated tras dismiss: tras una acción de consent, esperar (acotado, `consent_wait_ms`, default 5000ms) a que el texto del body se estabilice (Δ<5% en 2 polls). Sin acción → no se espera. Fail-open.
- **D35** — Iframes: reportar (`frames`) + fetch polite del src (`frameTexts`), robots-respecting, timeout, cap 5, texto 2000 chars, fail-open por frame. Sin vulnerar same-origin.
- **D36** — Anti-bot intermitente: retry 403/429 con tope (`anti_bot_retries`=2) y backoff largo (`anti_bot_backoff_s`=15s)+jitter. robots disallowed = hard stop. `retries` en el record.
```

(c) In the test-evidence section, add:

```markdown
- Fase 4 (cobertura): `uv run pytest tests/test_coverage.py -q` → 8 passed (unit iframes/config, e2e `/gated`, `/frames`, `/flaky403`, CSV `retries`).
```

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs(fase4): cobertura — gated consent, frames/frameTexts, retries (D34-D36)"
```

---

## Full-suite verification (after Task 5)

- [ ] `uv run pytest tests/ -q` → all green (63 previous + 8 new)
- [ ] `bash scripts/fixture-check.sh` → PASS
- [ ] If internet: `bash scripts/scenario-server.sh` → PASS (server regression)
- [ ] Contract: `bash ~/.claude/scripts/orpheus.sh https://www.python.org` → exit 0 + text
