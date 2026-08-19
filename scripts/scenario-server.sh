#!/usr/bin/env bash
# Scenario 6 — server mode: warm path, cache path, fallback a spawn.
# Self-contained: uses curl against `gscrape serve` and spawns `uv run gscrape`
# as the fallback. No external wrapper required.
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${GSCRAPE_PORT:-8799}"
URL="${1:-https://www.python.org}"
CACHE="work/scenario6-cache"
MAX_CHARS=800

mkdir -p work

# --- helper: POST /scrape, print record text, exit 0 only on clean text
server_scrape() {
  curl -sS --max-time 90 -H 'Content-Type: application/json' \
    -X POST -d "{\"url\":\"$URL\",\"options\":{\"maxTextChars\":$MAX_CHARS}}" \
    "http://127.0.0.1:${PORT}/scrape" || { echo "curl failed" >&2; return 1; }
}

server_text() {
  server_scrape | python3 -c '
import json, sys
try:
    rec = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not isinstance(rec, dict) or "url" not in rec:
    sys.exit(1)
if rec.get("protectionBlocked") or rec.get("error"):
    print("protected/error record", file=sys.stderr)
    sys.exit(1)
text = (rec.get("text") or "").strip()
if not text:
    print("empty text", file=sys.stderr)
    sys.exit(1)
print(text)
'
}

# --- fallback: spawn a cold `uv run gscrape` (same text contract)
spawn_text() {
  TMP="$(mktemp "${TMPDIR:-/tmp}/scenario6-XXXXXX.jsonl")"
  trap 'rm -f "$TMP"' RETURN
  uv run gscrape "$URL" -o "$TMP" --max-text-chars "$MAX_CHARS" --delay 0.3 --jitter 0.15 >/dev/null 2>&1 || return 1
  read -r line <"$TMP" || return 1
  [ -n "$line" ] || return 1
  python3 - "$line" <<'PY'
import json, sys
rec = json.loads(sys.argv[1])
if rec.get("protectionBlocked") or rec.get("error"):
    sys.exit(1)
text = (rec.get("text") or "").strip()
if not text:
    sys.exit(1)
print(text)
PY
}

echo "==> server: gscrape serve --port $PORT --cache-dir $CACHE"
uv run gscrape serve --port "$PORT" --cache-dir "$CACHE" &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT
sleep 4

echo "==> warm path (server arriba)"
OUT="$(server_text)" || { echo "FAIL: warm path exit != 0"; exit 1; }
echo "    warm text chars: ${#OUT}"

echo "==> cache path (2ª llamada al server)"
OUT2="$(server_text)" || { echo "FAIL: cache path exit != 0"; exit 1; }
[[ "$OUT2" == "$OUT" ]] || { echo "FAIL: cache path distinto del warm"; exit 1; }

echo "==> fallback a spawn (server muerto)"
kill "$SRV"
wait "$SRV" 2>/dev/null || true
trap - EXIT
OUT3="$(spawn_text)" || { echo "FAIL: spawn path exit != 0"; exit 1; }
[[ "$OUT3" == "$OUT" ]] || { echo "FAIL: spawn path distinto del server"; exit 1; }

echo "==> scenario 6 PASS"