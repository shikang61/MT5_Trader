#!/usr/bin/env bash
# Launch the portable MT5 terminal, then the mt5linux RPC server on :18812.
# WINEPREFIX is on the volume so the logged-in profile persists across redeploys.
set -euo pipefail
export WINEPREFIX="${WINEPREFIX:-/data/wine}" WINEDEBUG=-all DISPLAY=:0
source /opt/wine-mt5/winpython.env   # sets WINE_PYTHON

# First boot on a fresh volume: seed the prefix from the baked image install.
if [ ! -d "$WINEPREFIX/drive_c" ]; then
  mkdir -p "$WINEPREFIX"; cp -a /opt/wine-mt5/. "$WINEPREFIX"/
fi

TERM_EXE="$(find "$WINEPREFIX" -iname terminal64.exe | head -1)"
wine "$TERM_EXE" /portable &
sleep 15
# mt5linux server: Linux python module spawns the Wine python rpyc server.
exec uv run python -m mt5linux --host 0.0.0.0 -p "${MT5_BRIDGE_PORT:-18812}" -w wine "$WINE_PYTHON"
