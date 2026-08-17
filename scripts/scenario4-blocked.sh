#!/usr/bin/env bash
# Scenario 4 — strong protection (Cloudflare/DataDome): the tool must FAIL
# clearly and explicitly, not return garbage and not try to bypass.
set -uo pipefail
cd "$(dirname "$0")/.."

URL="${1:-https://nowsecure.nl}"
OUT="work/scenario4.jsonl"

mkdir -p work
echo "==> gscrape $URL (expect fail-closed)"
uv run gscrape "$URL" -o "$OUT" --delay 0.3 --jitter 0.2 -v

echo "==> protection check"
python3 - "$OUT" <<'PY'
import json, sys
r = json.loads(open(sys.argv[1]).read().splitlines()[0])
assert r.get("protectionBlocked") is True, "expected protectionBlocked=True"
assert (r.get("error") or "").startswith("PROTECTION_BLOCKED"), r.get("error")
print("OK  blocked clearly:", r["error"])
print("    text empty:", r["text"] == "")
PY
rc=$?
if [ $rc -ne 0 ]; then
    echo "FAIL: el sitio no bloqueó (puede pasar sin challenge, ej. nowsecure.nl es intermitente)"
    exit $rc
fi
echo "==> scenario 4 PASS (clear, explicit failure)"