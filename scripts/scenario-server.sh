#!/usr/bin/env bash
# Scenario 6 — server mode: warm path, cache path, fallback a spawn.
set -uo pipefail
cd "$(dirname "$0")/.."

ORPHEUS_SH="${ORPHEUS_SH:-$HOME/.claude/scripts/orpheus.sh}"
PORT="${GSCRAPE_PORT:-8799}"
URL="${1:-https://www.python.org}"
CACHE="work/scenario6-cache"

mkdir -p work

echo "==> server: gscrape serve --port $PORT --cache-dir $CACHE"
uv run gscrape serve --port "$PORT" --cache-dir "$CACHE" &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT
sleep 4

echo "==> warm path (server arriba)"
OUT="$(GSCRAPE_PORT="$PORT" "$ORPHEUS_SH" --max-chars 800 "$URL")" || { echo "FAIL: warm path exit != 0"; exit 1; }
[[ -n "$OUT" ]] || { echo "FAIL: warm path texto vacío"; exit 1; }
echo "    warm text chars: ${#OUT}"

echo "==> cache path (2ª llamada al server)"
OUT2="$(GSCRAPE_PORT="$PORT" "$ORPHEUS_SH" --max-chars 800 "$URL")" || { echo "FAIL: cache path exit != 0"; exit 1; }
[[ "$OUT2" == "$OUT" ]] || { echo "FAIL: cache path distinto del warm"; exit 1; }

echo "==> fallback a spawn (server muerto)"
kill "$SRV"
wait "$SRV" 2>/dev/null || true
trap - EXIT
OUT3="$(GSCRAPE_SKIP_SERVER=1 "$ORPHEUS_SH" --max-chars 800 "$URL")" || { echo "FAIL: spawn path exit != 0"; exit 1; }
[[ "$OUT3" == "$OUT" ]] || { echo "FAIL: spawn path distinto del server"; exit 1; }

echo "==> scenario 6 PASS"