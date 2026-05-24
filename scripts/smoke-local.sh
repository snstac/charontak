#!/usr/bin/env bash
# Operational smoke: TCP listener + charontak forward lane + idle config check.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CHARONTAK="${CHARONTAK:-$ROOT/.venv/bin/charontak}"
SMOKE_INI="$ROOT/examples/charontak.smoke.ini"
IDLE_INI="$ROOT/packaging/charontak.ini.example"
PORT=18087
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; kill $(jobs -p) 2>/dev/null || true' EXIT

echo "== charontak smoke test =="
echo "binary: $CHARONTAK"

echo "--- idle config (no enabled lanes) ---"
timeout 2 "$CHARONTAK" -c "$IDLE_INI" 2>&1 | tee "$TMP/idle.log" || true
grep -q "No enabled lanes" "$TMP/idle.log"

echo "--- forward lane: UDP ingress -> TCP :$PORT ---"
python3 - <<PY &
import asyncio, socket

async def serve():
    srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", $PORT)
    async with srv:
        await asyncio.sleep(8)

asyncio.run(serve())
PY
LISTENER=$!
sleep 0.5

timeout 5 "$CHARONTAK" -c "$SMOKE_INI" 2>&1 | tee "$TMP/lane.log" &
BRIDGE=$!
sleep 2
if grep -qE "Lane 'smoke' started|Lane .smoke. started" "$TMP/lane.log"; then
  echo "OK: smoke lane started"
else
  echo "FAIL: lane did not start" >&2
  cat "$TMP/lane.log" >&2
  exit 1
fi
kill "$BRIDGE" 2>/dev/null || true
wait "$BRIDGE" 2>/dev/null || true
kill "$LISTENER" 2>/dev/null || true

echo "== all smoke checks passed =="
