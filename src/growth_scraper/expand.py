"""P0: reveal collapsed content and trigger lazy/XHR-backed data.

Approach: semantic signals, not button text. `<details>` elements are opened by
setting `.open` (no click at all). `[aria-expanded="false"]`, `[aria-controls]`,
accordion/tab/toggle patterns inside EXCLUDED containers (cookies/consent) are
never touched. Every auto-click passes the click guard (blacklist +
cookie-container exclusion). While we click and scroll, the NetworkRecorder
watches the XHRs the page fires, so we learn what actually loads the content.
"""

from __future__ import annotations

from .clickguard import GUARD_JS
from .config import LOAD_MORE_TEXT_PATTERNS, ScrapeConfig

_EXPAND_JS_TMPL = """
(function (maxClicks) {
  var g = window.__gsGuard;
  var loadMoreTexts = %(load_more)r;
  var clicks = 0;

  function visible(el) {
    var r = el.getClientRects();
    return r.length > 0 && r[0].width > 0 && r[0].height > 0;
  }
  function tryClick(el) {
    if (!el || clicks >= maxClicks) return;
    if (el.__gsClicked) return;
    if (!g.safeToClick(el) || !visible(el)) return;
    try { el.click(); el.__gsClicked = true; clicks++; } catch (e) {}
  }

  // 1) <details> -> open via property, never a click.
  document.querySelectorAll('details').forEach(function (d) {
    if (!g.inCookieContainer(d) && !d.open) { try { d.open = true; } catch (e) {} }
  });

  // 2) Semantic expanders (aria / accordion / tab / toggle).
  document.querySelectorAll(
    '[aria-expanded="false"], [aria-controls], [role="tab"][aria-selected="false"], ' +
    '[class*="accordion"] button, [class*="accordion-title"], ' +
    '[class*="toggle"] button, [data-accordion]'
  ).forEach(function (el) { tryClick(el); });

  // 3) "Load more" list buttons (optional, guarded).
  document.querySelectorAll('button, a[role="button"], [role="button"]').forEach(function (el) {
    if (clicks >= maxClicks) return;
    if (!g.safeToClick(el) || !visible(el)) return;
    var txt = g.normalize((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || ''));
    for (var i = 0; i < loadMoreTexts.length; i++) {
      if (txt.indexOf(g.normalize(loadMoreTexts[i])) !== -1) { tryClick(el); break; }
    }
  });

  return clicks;
})(%(max_clicks)s);
"""

_EXPAND_JS = _EXPAND_JS_TMPL % {"load_more": LOAD_MORE_TEXT_PATTERNS, "max_clicks": "%s"}
_EXPAND_JS_STEPS = 5

_SCROLL_JS = """
window.__gsScrollStep = window.__gsScrollStep || 0;
(function () {
  var steps = %(steps)s;
  if (window.__gsScrollStep >= steps) return false;
  window.__gsScrollStep++;
  window.scrollTo(0, (window.__gsScrollStep / steps) * document.body.scrollHeight);
  return true;
})();
"""


def _expand_js(max_clicks: int) -> str:
    return _EXPAND_JS % max_clicks


async def expand_and_scroll(page, cfg: ScrapeConfig, netrec) -> dict:
    """Expand collapsed content and scroll to trigger lazy loads.

    Returns a small summary dict for verbose logging.
    """
    await page.evaluate(GUARD_JS)
    summary = {"expanded": 0, "scrolls": 0, "api_during_probe": 0}
    wait_ms = 350

    def api_seen_since(last: int) -> int:
        return netrec.count() - last

    # Initial expansion sweep.
    summary["expanded"] += await page.evaluate(_expand_js(cfg.max_expansions))
    await page.wait_for_timeout(wait_ms)
    await page.evaluate("window.__gsScrollStep = 0")

    # Scroll + re-sweep loop: lazy content often mounts new collapsed elements.
    for _ in range(_EXPAND_JS_STEPS):
        try:
            more = await page.evaluate(_SCROLL_JS % {"steps": _EXPAND_JS_STEPS})
        except Exception:
            more = False
        if not more:
            break
        summary["scrolls"] += 1
        await page.wait_for_timeout(250)
        before = netrec.count()
        summary["expanded"] += await page.evaluate(_expand_js(cfg.max_expansions))
        await page.wait_for_timeout(300)
        summary["api_during_probe"] = max(summary["api_during_probe"], api_seen_since(before))

    await page.wait_for_timeout(400)
    return summary