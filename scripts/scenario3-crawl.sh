#!/usr/bin/env bash
# Scenario 3 — crawl mode: respects the page cap and never leaves the seed domain.
set -uo pipefail
cd "$(dirname "$0")/.."

URL="${1:-https://en.wikipedia.org/wiki/Web_scraping}"
MAX="${2:-8}"
OUT="work/scenario3.jsonl"
SEED_HOST=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$URL').hostname or '')")

mkdir -p work
echo "==> crawl --max-pages $MAX from $URL"
uv run gscrape --crawl --max-pages "$MAX" "$URL" -o "$OUT" --delay 0.3 --jitter 0.2 -v

echo "==> cap + domain checks"
python3 - "$OUT" "$SEED_HOST" "$MAX" <<'PY'
import json, sys
from urllib.parse import urlparse
records = [json.loads(l) for l in open(sys.argv[1])]
host = sys.argv[2].lower().removeprefix("www.")
cap = int(sys.argv[3])
assert len(records) <= cap, f"cap violated: {len(records)} records (cap {cap})"
for r in records:
    h = (urlparse(r["url"]).hostname or "").lower()
    assert h == host or h.endswith("." + host), f"escaped domain: {r['url']}"
print(f"OK  {len(records)} records, all on {host} (cap {len(records)}/{cap})")
for r in records:
    print("   -", r["url"], "| err:", r["error"])
PY
rc=$?
if [ $rc -ne 0 ]; then
    echo "FAIL: cap o dominio no cumplidos"
    exit $rc
fi
echo "==> scenario 3 PASS (cap + same-domain only)"