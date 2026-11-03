#!/bin/sh
# NewGreedy — container entrypoint: fix volume ownership, then drop root
set -e
DATA="${NEWGREEDY_DATA_DIR:-/app/data}"
CONF="${NEWGREEDY_MITM_CONFDIR:-/home/newgreedy/.mitmproxy}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA" "$CONF"
  chown -R newgreedy:newgreedy "$DATA" "$CONF"
  exec setpriv --reuid=newgreedy --regid=newgreedy --init-groups "$@"
fi
exec "$@"
