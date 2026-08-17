"""Click guard: elements that must NEVER be auto-clicked.

A click on a real button (submit, add-to-cart, delete, login, accept-consent)
is unacceptable. This module centralises the blacklist and the cookie-container
exclusion used by both consent handling and content-expansion. It also exposes
a shared JS snippet so the same rules run inside the browser.
"""

from __future__ import annotations

import re
import unicodedata

from .config import CLICK_BLACKLIST, COOKIE_CONTAINER_SELECTORS

_CONTAINER_QUERY = ",".join(COOKIE_CONTAINER_SELECTORS)


def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text.lower()).strip()


def is_blacklisted(text: str) -> bool:
    t = normalize(text)
    for word in CLICK_BLACKLIST:
        if normalize(word) in t:
            return True
    return False


def container_query() -> str:
    return _CONTAINER_QUERY


# JS that lives in the page and enforces the same rules.
GUARD_JS = r"""
window.__gsGuard = {
  containerQuery: %(container_query)r,
  blacklist: %(blacklist)r,
  normalize: function (s) {
    s = s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    return s.toLowerCase().replace(/\s+/g, ' ').trim();
  },
  inCookieContainer: function (el) {
    return !!el.closest(this.containerQuery);
  },
  isBlacklisted: function (el) {
    var txt = (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '');
    var norm = this.normalize(txt);
    for (var i = 0; i < this.blacklist.length; i++) {
      if (norm.indexOf(this.normalize(this.blacklist[i])) !== -1) return true;
    }
    return false;
  },
  safeToClick: function (el) {
    if (!el || this.inCookieContainer(el)) return false;
    if (this.isBlacklisted(el)) return false;
    return true;
  }
};
""" % {"container_query": _CONTAINER_QUERY, "blacklist": CLICK_BLACKLIST}