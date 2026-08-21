"""Consent banner handling — reject only, never accept.

Crawl4AI's built-in `remove_consent_popups` clicks "Accept All" first, which
violates the guardrail. This module implements its own handler:

1. Look for a REJECT / decline / "only essential" button (CMP-specific
   selectors + multi-language text) inside consent containers -> click it.
2. If only accept + settings/preferences exist, open settings, then look for
   a reject/save-without-consent action inside the panel.
3. NEVER click anything that looks like acceptance.
4. Fallback: hide the container (DOM removal) purely to keep the extracted
   text clean. No clicks happen in that path.
"""

from __future__ import annotations

from .clickguard import container_query
from .config import ACCEPT_TEXT_PATTERNS, REJECT_TEXT_PATTERNS

_CMP_REJECT_SELECTORS = [
    "#onetrust-reject-all-handler",
    "#CybotCookiebotDialogBodyButtonDecline",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinDecline",
    "#didomi-notice-disagree-button",
    '[data-testid="didomi-notice-disagree-button"]',
    ".sp_choice_type_11",
    "#truste-reject-btn",
    '.truste-reject-btn',
    '[data-cookieconsent="reject"]',
    'button[id*="reject"]',
    'button[id*="decline"]',
]

_CMP_SETTINGS_SELECTORS = [
    "#onetrust-pc-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",  # NOT clicked; only opens panel on some CMPs
    "#didomi-notice-learn-more-button",
    '[data-testid="didomi-notice-learn-more-button"]',
    'button[id*="settings"]',
    'button[id*="preferences"]',
    'button[id*="config"]',
    'button[id*="show-options"]',
    'button[id*="manage"]',
]

_CMP_SAVE_SELECTORS = [
    "#onetrust-pc-sdk button.save-preference-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonAccept",  # never clicked
    "#didomi-preferences-save",
    'button[id*="save"]',
    'button[data-testid*="save"]',
]

_CONSENT_JS = r"""
(function () {
  var containerQuery = %(container_query)r;
  var rejectSelectors = %(cmp_reject)r;
  var settingsSelectors = %(cmp_settings)r;
  var saveSelectors = %(cmp_save)r;
  var rejectTexts = %(reject_text)r;
  var acceptTexts = %(accept_text)r;

  function normalize(s) {
    s = (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    return s.toLowerCase().replace(/\s+/g, ' ').trim();
  }
  function inContainer(el) {
    return !!el.closest(containerQuery);
  }
  function matchesAny(el, list) {
    var txt = normalize((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || ''));
    for (var i = 0; i < list.length; i++) {
      if (txt.indexOf(normalize(list[i])) !== -1) return true;
    }
    return false;
  }
  function clickFirst(el) {
    if (el) { try { el.click(); return true; } catch (e) {} }
    return false;
  }
  function findAction(selectors) {
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el && inContainer(el)) return el;
    }
    return null;
  }
  function findRejectByText() {
    var containers = document.querySelectorAll(containerQuery);
    for (var c = 0; c < containers.length; c++) {
      var els = containers[c].querySelectorAll('button, a[role="button"], [role="button"], a.btn, a.button, .btn');
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (matchesAny(el, acceptTexts)) continue;          // never accept
        if (matchesAny(el, rejectTexts)) return el;         // prefer first reject-like
      }
    }
    return null;
  }

  // Phase 1: direct reject button
  var target = findAction(rejectSelectors) || findRejectByText();
  if (target && clickFirst(target)) return 'rejected';

  // Phase 2: open settings, then look for a reject/save-without-consent
  var settings = findAction(settingsSelectors);
  if (settings && clickFirst(settings)) {
    var tries = 0;
    var rej = findAction(rejectSelectors) || findRejectByText();
    if (rej) { clickFirst(rej); return 'rejected-after-settings'; }
    var save = findAction(saveSelectors);
    if (save) {
      // only click save if it does NOT look like acceptance
      if (!matchesAny(save, acceptTexts)) { clickFirst(save); return 'settings-saved'; }
    }
  }

  // Phase 3: fallback — hide containers so text extraction stays clean.
  var hidden = 0;
  document.querySelectorAll(containerQuery).forEach(function (el) {
    el.style.setProperty('display', 'none', 'important');
    hidden++;
  });
  if (hidden) return 'hidden:' + hidden;
  return 'no-consent-found';
})();
""" % {
    "container_query": container_query(),
    "cmp_reject": _CMP_REJECT_SELECTORS,
    "cmp_settings": _CMP_SETTINGS_SELECTORS,
    "cmp_save": _CMP_SAVE_SELECTORS,
    "reject_text": REJECT_TEXT_PATTERNS,
    "accept_text": ACCEPT_TEXT_PATTERNS,
}


_STRONG_CONTAINER_SELECTORS = [
    '[class*="onetrust"]', '[id*="onetrust"]',
    '[class*="ot-sdk"]', '[id*="ot-sdk"]',
    '[class*="consent"]', '[id*="consent"]',
    '[class*="cmp"]', '[id*="cmp"]',
    '[class*="didomi"]', '[id*="didomi"]',
    '[class*="gdpr"]', '[id*="gdpr"]',
    '[class*="cookiebot"]', '[id*="CybotCookiebotDialog"]',
    '[class*="sp_message"]', '[id*="sp_message"]',
    '[class*="iubenda"]', '[id*="iubenda"]',
    '[class*="qc-cmp"]', '[id*="qc-cmp"]',
    '[class*="truste"]', '[id*="truste"]',
    '[class*="usercentrics"]', '[id*="usercentrics"]',
    '[class*="klaro"]', '[class*="osano"]', '[class*="complianz"]',
]

# `cookie` alone can match legitimate content (e.g. recipe pages), so it is
# only treated as a consent container when it holds interactive elements.
_WEAK_CONTAINER_SELECTORS = ['[class*="cookie"]', '[id*="cookie"]']

_REMOVE_JS = """
(function () {
  var strongQuery = %(strong)r;
  var weakQuery = %(weak)r;
  var removed = 0, hidden = 0;
  function hide(el) {
    el.style.setProperty('display', 'none', 'important');
    el.style.setProperty('visibility', 'hidden', 'important');
    el.setAttribute('aria-hidden', 'true');
  }
  function isOverlayTree(el) {
    var node = el;
    while (node) {
      var pos = '';
      try { pos = getComputedStyle(node).position; } catch (e) {}
      if (pos === 'fixed' || pos === 'absolute') return true;
      node = node.parentElement;
    }
    return false;
  }
  function handle(el) {
    if (isOverlayTree(el)) {
      // Inside a fixed/absolute dialog (SPA modal, e.g. Svelte/React consent
      // wall). Removing these orphans the framework's nodes and can break
      // rendering/hydration (seen live on ionos.es). Hiding is enough.
      hide(el); hidden++;
    } else {
      // Standalone static banner: safe to remove and guarantees a clean text.
      el.remove(); removed++;
    }
  }
  document.querySelectorAll(strongQuery).forEach(handle);
  document.querySelectorAll(weakQuery).forEach(function (el) {
    var hasUI = el.querySelector('button, input, select, a[role="button"], [role="button"]');
    if (hasUI) { handle(el); }
  });
  return (removed ? 'removed:' + removed : '') + (hidden ? ' hidden:' + hidden : '') || '0';
})();
""" % {"strong": ",".join(_STRONG_CONTAINER_SELECTORS), "weak": ",".join(_WEAK_CONTAINER_SELECTORS)}

# After a reject click: is any consent container still in the DOM? If yes, the
# reject was a no-op (e.g. static/fallback page) and we must clean it up.
_CONSENT_STILL_JS = """
(function () {
  var strongQuery = %(strong)r;
  var weakQuery = %(weak)r;
  function hasUI(el) {
    return !!el.querySelector('button, input, select, a[role="button"], [role="button"]');
  }
  return document.querySelectorAll(strongQuery).length +
         document.querySelectorAll(weakQuery).length;
})();
""" % {"strong": ",".join(_STRONG_CONTAINER_SELECTORS), "weak": ",".join(_WEAK_CONTAINER_SELECTORS)}


async def handle_consent(page, iterations: int = 3) -> str:
    """Run the reject-only handler a few times (banners can load late).

    If a reject action succeeds we stop touching the page — manipulating the DOM
    after the site dismissed the banner itself can break SPA frameworks (seen
    live on ionos.es). If no reject exists, or the reject was a no-op, we hide
    or remove consent containers so consent text never leaks into the output
    (hiding with CSS is not enough for static banners — page.content()
    serializes hidden nodes, so standalone in-flow banners are removed).
    Never clicks accept.
    """
    last = "no-consent-found"
    for i in range(iterations):
        try:
            last = await page.evaluate(_CONSENT_JS) or "no-consent-found"
        except Exception:
            return "error"
        # Some CMPs load their popup well after the page itself (Didomi, seen
        # live on decathlon.fr: the initial HTML only carries a `didomiApiKey`
        # config value, no popup markup yet — that's injected later by its
        # SDK). Only pay for this on the *second chance*, after a first quick
        # attempt already came back empty, and keep the bound modest (800ms,
        # down from an initial 2500ms): an unconditional up-front 2.5s wait
        # cost the whole test suite +137s (365s -> 502s), and even paying
        # that didn't end up fixing the motivating case below — a short,
        # second-chance-only wait is the honest trade-off for "some help with
        # slow CMPs" vs. "every page pays a tax for no confirmed benefit".
        #
        # Known unresolved case, not a regression: this does NOT fix
        # decathlon.fr. `wait_for_selector` finds *something* there (debugged
        # live), but it's some other element on the page that also matches
        # the broad `[class*="cookie"/"consent"/...]` selector — the real
        # `didomi-popup-*` element never attaches in a headless Orpheus run,
        # even though it renders fine in an interactive browser session. Root
        # cause not found (fingerprint-based suppression of the popup for
        # automated traffic is the working theory, unconfirmed). Not chased
        # further because it isn't actually harmful there: no consent text
        # leaked into `text` either way, and real content/structured data
        # still came through (verified live — see D44). Kept as a real,
        # modest improvement for CMPs that genuinely just load a bit late.
        if last == "no-consent-found" and i == 0:
            try:
                await page.wait_for_selector(container_query(), state="attached", timeout=800)
            except Exception:
                pass  # no CMP on this page, or it never showed up
            continue
        # If we clicked reject, let the site's own JS dismiss the banner; then
        # check it actually went away. Manipulating the DOM after a working
        # reject can break SPA frameworks (seen live on ionos.es), so we stop
        # here unless the reject was a no-op (static fallback page).
        if last.startswith("rejected"):
            await page.wait_for_timeout(400)
            try:
                still = await page.evaluate(_CONSENT_STILL_JS) or 0
            except Exception:
                still = 0
            if not still:
                return last
        try:
            removed = await page.evaluate(_REMOVE_JS) or 0
        except Exception:
            removed = 0
        if removed:
            last = f"{last}+removed:{removed}"
        if last.startswith("rejected") and not removed:
            return last
    return last