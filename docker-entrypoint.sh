#!/bin/sh
# Docker entrypoint for NewGreedy v1.1
# Usage:
#   docker run ... newgreedy/newgreedy          # standard mode
#   docker run ... newgreedy/newgreedy --mitmproxy  # mitmproxy mode

MITMPROXY_MODE=false
for arg in "$@"; do
  [ "$arg" = "--mitmproxy" ] && MITMPROXY_MODE=true
done

# Symlink data dir for persistence
ln -sf /app/data/newgreedy.log /app/newgreedy.log 2>/dev/null || true
ln -sf /app/data/stats.json    /app/stats.json    2>/dev/null || true

if [ "$MITMPROXY_MODE" = "true" ]; then
  echo "[entrypoint] Starting in mitmproxy mode..."
  exec mitmdump -p 3456 --ssl-insecure -s /app/newgreedy_addon.py
else
  echo "[entrypoint] Starting in standard mode..."
  exec python3 /app/newgreedy.py
fi
