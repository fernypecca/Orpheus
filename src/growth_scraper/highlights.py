"""Query-focused passage extraction (Context.dev `highlights` equivalent, local).

No LLM, no network: scores text blocks by query-token overlap and returns the
top N passages in page order. Fail-open: empty text/query yields [].
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9áéíóúñü]+", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def _tokens(query: str) -> list[str]:
    toks = [t.lower() for t in _WORD.findall(query or "")]
    return [t for t in toks if len(t) >= 2]


def _blocks(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n|\r\n\s*\r\n", text or "")
    out: list[str] = []
    for part in parts:
        part = " ".join(part.split())
        if not part:
            continue
        if len(part) > 600:
            out.extend(s.strip() for s in _SENT_SPLIT.split(part) if s and s.strip())
        else:
            out.append(part)
    return [b for b in out if len(b) >= 20]


def highlights_for_query(text: str, query: str, max_n: int = 3) -> list[str]:
    """Return up to max_n passages most relevant to query, in page order."""
    if not text or not query or max_n <= 0:
        return []
    try:
        toks = _tokens(query)
        if not toks:
            return []
        blocks = _blocks(text)
        if not blocks:
            return []
        scored: list[tuple[int, int, str]] = []
        for i, block in enumerate(blocks):
            low = block.lower()
            score = sum(low.count(t) for t in set(toks))
            if score > 0:
                # density bonus so a short on-point sentence beats a long page
                density = score / max(1, len(block.split()))
                scored.append((score, i, block))
                scored[-1] = (score * 1000 + int(density * 1000), i, block)
        scored.sort(key=lambda s: -s[0])
        picked = sorted(scored[:max_n], key=lambda s: s[1])
        return [b[:800] for _, _, b in picked]
    except Exception:
        return []
