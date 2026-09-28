#!/usr/bin/env bash
# Launch the Aeon Nimbus live platform: the interactive dashboard with the
# Data Studio (collect / upload / recalculate / export) wired to the API.
#
#   ./run.sh            # serves on http://127.0.0.1:5174 and opens the browser
#   PORT=9000 ./run.sh  # pick another port
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-5174}"
URL="http://127.0.0.1:${PORT}/"

echo "Aeon Nimbus — starting on ${URL}"
echo "  · dashboard + Data Studio (live)   · API docs at ${URL}docs"
echo "  (Ctrl-C to stop)"

# open the browser once the server is up (best-effort; harmless if it fails)
( for _ in $(seq 1 40); do
    if curl -sf "${URL}api/health" >/dev/null 2>&1; then
      command -v open >/dev/null && open "${URL}" || { command -v xdg-open >/dev/null && xdg-open "${URL}"; }
      break
    fi
    sleep 0.5
  done ) &

exec python3 -m uvicorn aeon_nimbus.api:app --host 127.0.0.1 --port "${PORT}"
