#!/usr/bin/env bash
# Container entrypoint — runs before supervisord. Creates the persistent-volume
# dirs and routes the app's cwd-relative logs/ and data/ onto /data so trade
# logs, single-instance locks, and tick parquet survive redeploys, and so
# supervisord can open its own logfile under /data/logs on a fresh volume.
set -e
mkdir -p /data/logs /data/data /data/wine
[ -L /app/logs ] || { rm -rf /app/logs; ln -s /data/logs /app/logs; }
[ -L /app/data ] || { rm -rf /app/data; ln -s /data/data /app/data; }
exec "$@"
