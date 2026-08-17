#!/usr/bin/env bash
# Scenario 1 — simple site, no protection. Validates basic scrape + JSONL format.
set -uo pipefail
cd "$(dirname "$0")/.."

URL="${1:-https://example.com}"
OUT="work/scenario1.jsonl"

mkdir -p work
echo "==> gscrape $URL"
uv run gscrape "$URL" -o "$OUT" --delay 0.3 --jitter 0.2 -v

echo "==> output: $OUT"
python3 - "$OUT" <<'PY'
import json, sys
lines = [json.loads(l) for l in open(sys.argv[1])]
assert len(lines) == 1, f"expected 1 record, got {len(lines)}"
r = lines[0]
for field in ("url", "title", "text", "apiResponses", "pageType", "error"):
    assert field in r, f"missing field {field}"
print("OK  record:", r["url"])
print("    title:", r["title"][:60])
print("    text chars:", len(r["text"]))
print("    error:", r["error"])
PY
echo "==> scenario 1 PASS"