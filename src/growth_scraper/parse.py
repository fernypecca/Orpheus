"""CSS-selector field extraction (Context.dev `parse` equivalent, local + polite).

Rules: {"title": "h1", "prices": ".price[]"} — a selector ending in `[]`
returns a list of all matches, otherwise the first match. Fail-open: any
error or empty HTML yields {} (never raises). Unmatched single fields are
None, unmatched list fields are [].
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def parse_fields(html: str, rules: dict[str, str] | None) -> dict:
    """Extract named fields from raw HTML with CSS selectors."""
    if not html or not rules:
        return {}
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        out: dict = {}
        for name, selector in rules.items():
            if not name or not selector:
                continue
            try:
                if selector.endswith("[]"):
                    sel = selector[:-2].strip()
                    if not sel:
                        continue
                    els = soup.select(sel)
                    out[name] = [_clean(el.get_text(" ", strip=True)) for el in els]
                    out[name] = [t for t in out[name] if t]
                else:
                    el = soup.select_one(selector)
                    out[name] = _clean(el.get_text(" ", strip=True)) if el else None
                    if out[name] == "":
                        out[name] = None
            except Exception:
                out[name] = [] if selector.endswith("[]") else None
        return out
    except Exception:
        return {}


def parse_rules_from_cli(values: list[str] | None) -> dict[str, str]:
    """Parse `--parse name=selector` repeats into a rules dict."""
    rules: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            continue
        name, selector = item.split("=", 1)
        name, selector = name.strip(), selector.strip()
        if name and selector:
            rules[name] = selector
    return rules
