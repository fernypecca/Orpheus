# gscrape — polite, LLM-ready web scraper

Generic web scraper for growth-marketing research (competitors, provider
directories, marketplaces). Built on **Crawl4AI 0.9.x** (Playwright underneath).
Cost ≈ $0: runs on your machine and produces JSONL that feeds free LLMs
(Nemotron vía NVIDIA, Groq) directly.

## Install

Requires Python 3.12+ and `uv` (or pip).

```bash
uv sync                 # installs deps (crawl4ai, playwright)
uv run playwright install chromium   # browser engine
uv run gscrape --help
```

## Quick start

```bash
# One page
uv run gscrape https://competitor.com/proveedores -o out.jsonl

# Several pages / from a file
uv run gscrape https://a.com https://b.com -o out.jsonl
uv run gscrape --urls-file urls.txt -o out.jsonl

# Follow same-domain links (BFS), capped
uv run gscrape --crawl --max-pages 50 https://marketplace.com -o out.jsonl

# Bypass robots.txt (default: respected), tune politeness
uv run gscrape https://site.com -o out.jsonl --ignore-robots --delay 0.5 --jitter 0.3

# Local record cache — re-runs don't reprocess
uv run gscrape https://site.com -o out.jsonl --cache-dir .cache

# Save page images to disk (multimodal pointer for e.g. Nemotron 3)
uv run gscrape https://site.com -o out.jsonl --export-images assets/
```

Output is **JSONL, one record per page**:

```json
{
  "url": "https://competitor.com/proveedores",
  "title": "Directorio de proveedores",
  "text": "...contenido visible limpio (sin nav/footer/scripts/cookies)...",
  "apiResponses": [{"url": "https://competitor.com/api/v1/search?page=2", "body": {...}}],
  "pageType": "listing",
  "items": [{"title": "...", "href": "...", "snippet": "..."}],
  "images": [],
  "error": null,
  "crawledFrom": null,
  "scrapedAt": "2026-08-17T16:48:52+00:00",
  "statusCode": 200,
  "protectionBlocked": false
}
```

The downstream `llm-batch.py` reads `--jsonl-field text` — the field name is
**`text`**, unchanged.

## CLI

| Flag | Default | Meaning |
|---|---|---|
| `urls` / `--urls-file` | — | target URLs (space-separated or file, `#` comments ok) |
| `--crawl`, `--max-pages N` | — / 50 | follow same-domain links, page cap |
| `-o, --output` | `out.jsonl` | output file |
| `--delay` / `--jitter` | 0.4 / 0.2 s | polite pacing; `robots.txt Crawl-delay` wins if higher |
| `--ignore-robots` | off | bypass robots.txt (default: respected) |
| `--cache-dir` | — | local record + robots cache (2nd run skips reprocessing) |
| `--no-expand` | off | skip collapsed-content/scroll probing |
| `--no-apis` | off | skip `apiResponses` capture + pagination replay |
| `--no-consent` | off | skip consent-banner rejection/removal |
| `--export-images DIR` | — | save images to DIR (multimodal pointer) |
| `--max-api-responses` | 30 | cap of `apiResponses` per record |
| `--page-timeout` | 30000 ms | page load timeout |
| `--headful`, `-v` | — | show browser / verbose logs |

## Guarantees (non-negotiables)

- **robots.txt respected by default**; explicit `--ignore-robots` to bypass.
- **Polite delays** between requests (base + random jitter).
- **Zero CAPTCHA solving / anti-bot evasion.** If a site defends itself
  (Cloudflare, DataDome, Akamai…) the record carries
  `"error": "PROTECTION_BLOCKED: ..."`, `protectionBlocked: true`, empty text.
- **Consent banners: rejected, never accepted.** We click the "reject / only
  essential" path (OneTrust, Cookiebot, Didomi, …); if none exists we remove
  the container from the DOM purely to keep `text` clean. We never click
  "Accept All".
- **Auto-click blacklist**: buy, checkout, delete, cancel, submit, subscribe,
  login, accept-consent, etc. — multi-language. Auto-clicks only target
  semantic expanders (`<details>`, `aria-expanded=false`, accordions, "load
  more") and never touch cookie/consent containers.
- **Colapsed content (P0)**: we click semantic expanders, scroll for lazy
  loads, AND capture the network requests/responses they trigger — so content
  that lives behind an internal API (not the DOM) is still captured in
  `apiResponses`.

## The 5 test scenarios

```bash
# 1. Simple site → well-formed JSONL
scripts/scenario1-simple.sh                  # e.g. https://example.com

# 2. Strong cookie banner → zero consent noise in `text`
scripts/scenario2-cookies.sh                 # e.g. https://www.britannica.com (OneTrust)

# 3. Crawl cap + same-domain only
scripts/scenario3-crawl.sh                   # seed + --max-pages

# 4. Strong protection → clear, explicit failure
scripts/scenario4-blocked.sh                 # e.g. https://nowsecure.nl

# 5. Cache → second run doesn't reprocess
scripts/scenario5-cache.sh                   # --cache-dir

# Offline deterministic version of all 5 (local fixture server, no internet):
scripts/fixture-check.sh
```

## Limitations / notes

- Consent banners living inside **cross-origin iframes** can't be removed from
  the main frame; they also aren't captured in `text` (iframes are not
  processed by default).
- Bespoke **SPA consent walls** (e.g. ionos.es's Svelte modal) can leak their
  own text into the output and can't always be cleaned without accepting — and
  we never accept. Standard CMPs (OneTrust, Cookiebot, Didomi) are handled
  cleanly.
- Anti-bot sites are intermittent: the same URL may pass or challenge on any
  given run. Fail-closed detection is verified deterministically by the
  offline fixture suite.
- `--export-images` downloads images over the network; on protected sites or
  if the host blocks hotlinking, images may be skipped.
- Live scenario scripts assume working internet; `scripts/fixture-check.sh`
  validates everything deterministically offline.

## License / credits

Apache-2.0. Built on [Crawl4AI](https://github.com/unclecode/crawl4ai).