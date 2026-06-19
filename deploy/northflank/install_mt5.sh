#!/usr/bin/env bash
# Build-time: install account B's broker MT5 terminal + a Windows Python (with
# MetaTrader5 + mt5linux) into the Wine prefix. The terminal is installed here;
# the logged-in profile is provided at runtime on the persistent volume.
set -euxo pipefail
export WINEPREFIX=/opt/wine-mt5 WINEARCH=win64 WINEDEBUG=-all

wineboot --init && sleep 5

# Broker B's own terminal installer (REPLACE with B-broker's download URL).
curl -L -o /tmp/mt5setup.exe "${MT5_INSTALLER_URL:?set MT5_INSTALLER_URL at build}"
xvfb-run -a wine /tmp/mt5setup.exe /auto || true   # silent where supported
sleep 20

# Windows Python under Wine, then MetaTrader5 + mt5linux into it (the bridge SERVER).
curl -L -o /tmp/py.exe "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
xvfb-run -a wine /tmp/py.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 || true
sleep 20
WINPY="$(find "$WINEPREFIX" -name python.exe -path '*Python3*' | head -1)"
xvfb-run -a wine "$WINPY" -m pip install --no-cache-dir MetaTrader5 mt5linux
echo "WINE_PYTHON=$WINPY" > /opt/wine-mt5/winpython.env
