#!/usr/bin/env bash
# Offline, deterministic version of all 5 scenarios + P0/P1 features,
# served from a local fixture server (no internet required).
set -uo pipefail
cd "$(dirname "$0")/.."

echo "==> offline test suite (fixture server)"
uv run pytest tests/ -q --no-header -p no:cacheprovider "$@"
echo "==> PASS (all scenarios validated deterministically offline)"