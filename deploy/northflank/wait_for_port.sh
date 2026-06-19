#!/usr/bin/env bash
# Block until host:port accepts a TCP connection, then exec the rest of the args.
# Used so the Python clients never import the gateway before the bridge is up.
set -euo pipefail
host="${MT5_BRIDGE_HOST:-localhost}"; port="${1:?port}"; shift
for _ in $(seq 1 120); do
  (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null && { exec 3>&-; exec "$@"; }
  sleep 2
done
echo "wait_for_port: ${host}:${port} never came up" >&2; exit 1
