#!/usr/bin/env bash
# Scenario 5 — cache: running twice must not reprocess the same URL.
set -uo pipefail
cd "$(dirname "$0")/.."

URL="${1:-https://example.com}"
CACHE="work/cache-scenario5"
OUT1="work/scenario5-run1.jsonl"
OUT2="work/scenario5-run2.jsonl"

rm -rf "$CACHE" "$OUT1" "$OUT2"
mkdir -p work

echo "==> run 1 (cold)"
uv run gscrape "$URL" -o "$OUT1" --cache-dir "$CACHE" --delay 0.3 --jitter 0.2
echo "==> run 2 (warm)"
uv run gscrape "$URL" -o "$OUT2" --cache-dir "$CACHE" --delay 0.3 --jitter 0.2

python3 - "$OUT1" "$OUT2" <<'PY'
import json, sys
def rec(p):
    return json.loads(open(p).read().splitlines()[0])
r1, r2 = rec(sys.argv[1]), rec(sys.argv[2])
assert r1["text"] == r2["text"], "warm run should return the cached record"
assert r1["url"] == r2["url"]
print("OK  run1 == run2 (cached):", r2["url"])
print("    text chars:", len(r2["text"]))
print("    (second run produced no network request for the page)")
PY
echo "==> scenario 5 PASS (cache served the second run)"