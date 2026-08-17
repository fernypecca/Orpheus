#!/usr/bin/env bash
# Scenario 2 — strong cookie banner (OneTrust/Didomi/Cookiebot site).
# PASS condition: extracted text contains ZERO consent noise.
set -uo pipefail
cd "$(dirname "$0")/.."

URL="${1:-https://www.britannica.com}"
OUT="work/scenario2.jsonl"

mkdir -p work
echo "==> gscrape $URL"
uv run gscrape "$URL" -o "$OUT" --delay 0.4 --jitter 0.2 -v

echo "==> consent-noise check"
python3 - "$OUT" <<'PY'
import json, sys, re
r = json.loads(open(sys.argv[1]).read().splitlines()[0])
text = r["text"]
banned = ["consentimiento", "propósitos", "iab", "rechazar", "aceptar todo",
          "accept all", "cookie policy", "gestionar cookies", "configurar cookies",
          "reject all", "decline"]
leaks = [w for w in banned if w in text.lower()]
if leaks:
    print("FAIL banner noise leaked:", leaks)
    sys.exit(1)
print("OK  no consent noise in", len(text), "chars of text")
print("    title:", r["title"][:60])
PY
rc=$?
if [ $rc -ne 0 ]; then
    echo "FAIL: ruido de consentimiento en el texto (el handler reject-only no pudo limpiarlo)"
    exit $rc
fi
echo "==> scenario 2 PASS (no cookie-banner noise)"