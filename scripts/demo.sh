#!/usr/bin/env bash
# demo.sh — Orpheus 1-click demo for Codespaces.
# Scrapes a real site, prints the clean LLM-ready text, then shows the record summary.
#
# Usage: scripts/demo.sh [URL]     (default: https://www.python.org)
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${1:-https://www.python.org}"
OUT="work/demo.jsonl"
mkdir -p work

if ! uv run python -c "import crawl4ai" >/dev/null 2>&1; then
  echo "==> installing deps (first boot, one time)…"
  uv sync
  uv run playwright install chromium
fi

echo "==> Orpheus: scraping $URL"
echo "    (cold start can take ~10-30s — the browser engine spins up once)"
uv run gscrape "$URL" -o "$OUT" --max-text-chars 6000 --delay 0.3 --jitter 0.15

echo
echo "==> Clean LLM-ready text (first 40 lines):"
python3 - "$OUT" <<'PY'
import json, sys
rec = json.loads(open(sys.argv[1]).readline())
text = rec.get("text") or ""
print("\n".join(text.splitlines()[:40]))
print(f"\n[... {len(text):,} chars total]")
PY

echo
echo "==> Record summary:"
python3 - "$OUT" <<'PY'
import json, sys
rec = json.loads(open(sys.argv[1]).readline())
s = rec.get("summary") or {}
print(f"  url:       {rec.get('url')}")
print(f"  title:     {s.get('title')}")
print(f"  language:  {s.get('language')}")
print(f"  words:     {s.get('wordCount')}")
print(f"  status:    {rec.get('statusCode')}   protected: {rec.get('protectionBlocked')}")
print(f"  error:     {rec.get('error')}")
PY

echo
echo "==> Full JSONL record written to $OUT"
echo "    (inspect it: cat work/demo.jsonl | python3 -m json.tool)"