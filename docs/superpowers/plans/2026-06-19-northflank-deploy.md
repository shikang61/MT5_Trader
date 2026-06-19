# Northflank Deploy (Account B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the full live stack (portfolio bot + tick logger + weekly calendar fetch + dashboard) for a second MT5 account (account B, different broker) in one self-contained Linux container on Northflank, reaching the Wine-hosted MT5 terminal through the `mt5linux` RPC bridge.

**Architecture:** One all-in-one container, one persistent volume. `supervisord` runs Xvfb → Wine MT5 terminal → `mt5linux` RPC server (:18812), then native-Linux Python processes (bot, tick logger, dashboard) that reach MT5 over rpyc. A merge-safe backend shim picks `MetaTrader5` (Windows) vs `mt5linux` (Linux) at import. Everything else is new files so the `deploy/northflank` branch stays ~99% additive.

**Tech Stack:** Python ≥3.11, `uv` (native Linux deps), Wine (winehq-stable), `mt5linux` (Linux client + Wine-python server), `supervisord`, `supercronic`, Xvfb/x11vnc/noVNC, Streamlit, Northflank Combined service + persistent volume + secret group.

## Global Constraints

- **Branch:** `deploy/northflank`, divergent, does NOT merge to `main` during the competition (live Jun 15–27 2026).
- **Additive only:** new files except ONE merge-safe edit to `src/mt5_trader/live/mt5_gateway.py` (no-op on Windows) plus a one-line `reconnect()` branch.
- **`mt5linux` is NOT a pyproject dependency** — Linux-only, installed in the Dockerfile. `pyproject.toml` is not modified.
- **Secrets** (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) only via env / Northflank secret group — never committed, never baked into image layers.
- **Python ≥3.11.** Native Linux Python for the app; a separate Windows Python under Wine hosts `MetaTrader5` for the bridge server.
- **Account B is a different broker:** its own MT5 installer, its own symbol strings (`config/portfolio.northflank.yaml`), possibly a different order filling mode — order round-trip is a hard gate.
- All four mt5-touching paths share ONE terminal session; `mt5linux` serializes calls. Read-clients (tick logger, dashboard) stay low-frequency.

---

### Task 1: Backend shim + merge-safe gateway edit

**Files:**
- Create: `src/mt5_trader/live/mt5_backend.py`
- Modify: `src/mt5_trader/live/mt5_gateway.py:16-19` (import block) and `:28-29` (None-message)
- Test: `tests/test_mt5_backend.py`

**Interfaces:**
- Produces: module `mt5_trader.live.mt5_backend` exporting `mt5` (the backend object or `None`) and `IS_BRIDGE: bool`. `mt5_gateway` consumes both via `from .mt5_backend import mt5, IS_BRIDGE`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mt5_backend.py
"""mt5_backend selects the right MT5 backend. On a dev box with neither the
win32 MetaTrader5 package nor a reachable bridge, mt5 is None and IS_BRIDGE
reflects the env flag — and the gateway raises a clear error rather than
crashing at import."""
import importlib
import sys

import pytest


def _reload_backend(monkeypatch, bridge: bool):
    if bridge:
        monkeypatch.setenv("MT5_BRIDGE", "1")
    else:
        monkeypatch.delenv("MT5_BRIDGE", raising=False)
    sys.modules.pop("mt5_trader.live.mt5_backend", None)
    return importlib.import_module("mt5_trader.live.mt5_backend")


def test_backend_none_without_mt5_or_bridge(monkeypatch):
    # No MetaTrader5 wheel on the dev mac, no bridge up -> mt5 is None, no crash.
    backend = _reload_backend(monkeypatch, bridge=False)
    assert backend.mt5 is None
    assert backend.IS_BRIDGE is False


def test_is_bridge_reads_env(monkeypatch):
    backend = _reload_backend(monkeypatch, bridge=True)
    assert backend.IS_BRIDGE is True


def test_gateway_raises_clear_error_when_backend_none(monkeypatch):
    _reload_backend(monkeypatch, bridge=False)
    sys.modules.pop("mt5_trader.live.mt5_gateway", None)
    gw_mod = importlib.import_module("mt5_trader.live.mt5_gateway")
    with pytest.raises(gw_mod.MT5Error):
        gw_mod.MT5Gateway()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mt5_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: mt5_trader.live.mt5_backend`.

- [ ] **Step 3: Create the backend module**

```python
# src/mt5_trader/live/mt5_backend.py
"""Pick the MetaTrader5 backend for this platform and export it as `mt5`.

Windows (Azure VPS): the native win32 `MetaTrader5` package.
Linux (Northflank): the `mt5linux` RPC client, which proxies the same API to a
MetaTrader5 instance running under Wine on the same host (server on :18812).

Importing this module never raises: if neither backend is reachable (e.g. a dev
mac, or the bridge is not up yet) `mt5` is None and MT5Gateway() raises a clear
error. `IS_BRIDGE` is True when running against the Linux bridge — the gateway
uses it to pick its reconnect strategy.
"""
from __future__ import annotations

import os

IS_BRIDGE = bool(os.environ.get("MT5_BRIDGE"))

try:
    import MetaTrader5 as mt5            # Windows: native win32 package
except ImportError:                      # Linux/Northflank: mt5linux RPC bridge
    try:
        from mt5linux import MetaTrader5
        mt5 = MetaTrader5(
            host=os.environ.get("MT5_BRIDGE_HOST", "localhost"),
            port=int(os.environ.get("MT5_BRIDGE_PORT", "18812")),
        )
    except Exception:                    # mt5linux absent, or bridge unreachable
        mt5 = None
```

- [ ] **Step 4: Edit the gateway to consume the backend**

In `src/mt5_trader/live/mt5_gateway.py`, replace lines 16-19:

```python
try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None
```

with:

```python
from .mt5_backend import mt5, IS_BRIDGE
```

and change the `__init__` guard message (lines 28-29) from:

```python
        if mt5 is None:
            raise MT5Error("MetaTrader5 package not available — run on the Windows VPS")
```

to:

```python
        if mt5 is None:
            raise MT5Error("no MT5 backend — run on the Windows VPS, or start the "
                           "mt5linux bridge (set MT5_BRIDGE + bring :18812 up)")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mt5_backend.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full suite — nothing else broke**

Run: `uv run pytest -q`
Expected: PASS (existing tests green; `broker_pnl` etc. unaffected).

- [ ] **Step 7: Commit**

```bash
git add src/mt5_trader/live/mt5_backend.py src/mt5_trader/live/mt5_gateway.py tests/test_mt5_backend.py
git commit -m "feat(gateway): platform backend shim (MetaTrader5 | mt5linux)"
```

---

### Task 2: Linux fatal-on-bridge-loss reconnect

**Files:**
- Modify: `src/mt5_trader/live/mt5_gateway.py:49-54` (`reconnect`)
- Test: `tests/test_gateway_reconnect.py`

**Interfaces:**
- Consumes: `IS_BRIDGE` from Task 1.
- Produces: `MT5Gateway.reconnect()` — on the bridge, raises `SystemExit` (escapes `resilient_loop`'s `except Exception` → process exits → supervisord restarts with a fresh rpyc connection). On Windows, unchanged (`shutdown()` + `initialize()`).

**Why SystemExit:** `runner.resilient_loop` (runner.py:133) wraps `reconnect()` in `except Exception`. `SystemExit` derives from `BaseException`, so it is NOT caught — it propagates out, the process exits non-zero, and supervisord relaunches the bot. A fresh `import mt5_backend` then rebuilds the dead rpyc connection. Reviving a dead rpyc socket in-process (the Windows path) is unreliable, so we don't try.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gateway_reconnect.py
"""reconnect(): bridge mode is fatal-to-process (SystemExit so supervisord
relaunches with a fresh rpyc connection); Windows mode re-initializes in place."""
import importlib
import sys

import pytest


def _load_gateway(monkeypatch, bridge: bool):
    monkeypatch.setenv("MT5_BRIDGE", "1") if bridge else monkeypatch.delenv("MT5_BRIDGE", raising=False)
    sys.modules.pop("mt5_trader.live.mt5_backend", None)
    sys.modules.pop("mt5_trader.live.mt5_gateway", None)
    return importlib.import_module("mt5_trader.live.mt5_gateway")


class _FakeMT5:
    def __init__(self):
        self.calls = []
        self.TIMEFRAME_M1 = self.TIMEFRAME_M5 = self.TIMEFRAME_M15 = self.TIMEFRAME_H1 = 0

    def initialize(self, **kw):
        self.calls.append(("initialize", kw))
        return True

    def shutdown(self):
        self.calls.append(("shutdown", {}))


def test_reconnect_bridge_is_fatal(monkeypatch):
    gw_mod = _load_gateway(monkeypatch, bridge=True)
    fake = _FakeMT5()
    monkeypatch.setattr(gw_mod, "mt5", fake)
    gw = gw_mod.MT5Gateway()
    gw._creds = {"login": 1, "password": "x", "server": "s"}
    with pytest.raises(SystemExit):
        gw.reconnect()


def test_reconnect_windows_reinitializes(monkeypatch):
    gw_mod = _load_gateway(monkeypatch, bridge=False)
    fake = _FakeMT5()
    monkeypatch.setattr(gw_mod, "mt5", fake)
    gw = gw_mod.MT5Gateway()
    gw._creds = {"login": 1, "password": "x", "server": "s"}
    gw.reconnect()
    assert ("shutdown", {}) in fake.calls
    assert any(c[0] == "initialize" for c in fake.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gateway_reconnect.py -v`
Expected: FAIL — `test_reconnect_bridge_is_fatal` does not raise SystemExit (current reconnect calls shutdown/initialize in both modes).

- [ ] **Step 3: Edit `reconnect`**

Replace `src/mt5_trader/live/mt5_gateway.py` lines 49-54:

```python
    def reconnect(self) -> None:
        """Re-attach after the terminal restarted (e.g. auto-update) — the
        IPC pipe dies with (-10001, 'IPC send failed')."""
        mt5.shutdown()
        if not mt5.initialize(**self._creds):
            raise MT5Error(f"reconnect failed: {mt5.last_error()}")
```

with:

```python
    def reconnect(self) -> None:
        """Re-attach after the terminal restarted (e.g. auto-update) — the
        IPC pipe dies with (-10001, 'IPC send failed').

        On the Linux bridge the rpyc socket dies with the terminal and cannot be
        revived in-process; exit so supervisord relaunches us with a fresh
        rpyc connection (single-instance lock releases on exit, so no dup)."""
        if IS_BRIDGE:
            raise SystemExit("[mt5_gateway] bridge connection lost — exiting for "
                             "supervisord to relaunch with a fresh rpyc link")
        mt5.shutdown()
        if not mt5.initialize(**self._creds):
            raise MT5Error(f"reconnect failed: {mt5.last_error()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway_reconnect.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/mt5_gateway.py tests/test_gateway_reconnect.py
git commit -m "feat(gateway): bridge reconnect exits for supervisord relaunch"
```

---

### Task 3: Account-B portfolio config + symbol discovery

**Files:**
- Create: `config/portfolio.northflank.yaml`
- Uses: `scripts/check_symbols.py` (existing) for discovery.

**Interfaces:**
- Consumes: nothing new.
- Produces: `config/portfolio.northflank.yaml` — same books/weights as `config/portfolio.yaml`, but `symbol`/`symbol2` strings replaced with **account-B-broker** names. `run_portfolio.py --portfolio config/portfolio.northflank.yaml` loads it.

- [ ] **Step 1: Copy the live config as the starting point**

```bash
cp config/portfolio.yaml config/portfolio.northflank.yaml
```

Add this header comment at the top of `config/portfolio.northflank.yaml` (above `magic:`):

```yaml
# Account B (Northflank, DIFFERENT BROKER). Same books/weights as portfolio.yaml;
# symbol strings are this broker's names (gold/silver may not be XAUUSD/XAGUSD).
# Discover the real names with scripts/check_symbols.py against terminal B
# (Step 2) and replace every symbol/symbol2 below before going live.
```

- [ ] **Step 2: Discover account-B symbol names (run AFTER the container's terminal is logged in — Task 7 first-login)**

Run inside the container (or any host with the bridge up), with B's creds in env:

```bash
uv run python scripts/check_symbols.py
```

Expected: prints the broker's gold/silver symbol strings (e.g. `GOLD`, `XAUUSD.r`, `XAGUSD.m`). Note them.

- [ ] **Step 3: Replace symbol strings**

Edit `config/portfolio.northflank.yaml`: set every `symbol:` and `symbol2:` to the names from Step 2. (If gold is `GOLD`, replace all six `symbol: XAUUSD` and the `symbol2: XAGUSD` accordingly.)

- [ ] **Step 4: Validate it loads**

Run: `uv run python -c "import yaml; d=yaml.safe_load(open('config/portfolio.northflank.yaml')); print(sum(float(b.get('weight',1)) for b in d['books']))"`
Expected: prints a number in `[0.5, 1.5]` (weight sum sanity, same gate as `run_portfolio.load_books`).

- [ ] **Step 5: Commit** (commit the file now with the placeholder names noted; symbols get finalized during Task 7 bring-up)

```bash
git add config/portfolio.northflank.yaml
git commit -m "feat(config): account-B portfolio config (broker-B symbols)"
```

---

### Task 4: `Dockerfile.northflank` — Wine + MT5 + bridge image

**Files:**
- Create: `Dockerfile.northflank`
- Create: `deploy/northflank/install_mt5.sh`

**Interfaces:**
- Produces: an image that has Wine + a Windows Python (with `MetaTrader5` + `mt5linux`) under `WINEPREFIX`, the native-Linux project installed via `uv`, `mt5linux` in the Linux env, and Xvfb/x11vnc/noVNC available. The terminal **installer** is baked; the **login/profile** is supplied at runtime on the volume.

> **Verify-at-impl (the one genuinely unknown):** the exact `mt5linux` server invocation and the Wine MetaTrader5 install path. The commands below are the best-known form; Step 4's verify loop is how you confirm/adjust them. This is a real task with a verification gate, not a guess to leave unchecked.

- [ ] **Step 1: Write the MT5 install helper**

```bash
# deploy/northflank/install_mt5.sh
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
```

- [ ] **Step 2: Write `Dockerfile.northflank`**

```dockerfile
# Dockerfile.northflank — account B live stack on Linux via Wine + mt5linux.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive WINEPREFIX=/opt/wine-mt5 WINEARCH=win64 WINEDEBUG=-all
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg software-properties-common \
      xvfb x11vnc novnc websockify fluxbox \
      supervisor python3 python3-venv python3-pip git \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor -o /etc/apt/keyrings/winehq.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/winehq.gpg] https://dl.winehq.org/wine-builds/ubuntu/ noble main" \
      > /etc/apt/sources.list.d/winehq.list \
 && apt-get update && apt-get install -y --install-recommends winehq-stable \
 && rm -rf /var/lib/apt/lists/*

# uv (native Linux deps for the app — NOT under Wine)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# supercronic (cron in containers, for the weekly calendar fetch)
RUN curl -fsSLo /usr/local/bin/supercronic \
      https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64 \
 && chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --no-dev && uv pip install mt5linux   # mt5linux = Linux CLIENT, not in pyproject

# Bake the broker terminal + Wine-python bridge server.
COPY deploy/northflank/install_mt5.sh /tmp/install_mt5.sh
ARG MT5_INSTALLER_URL
RUN MT5_INSTALLER_URL="${MT5_INSTALLER_URL}" bash /tmp/install_mt5.sh

COPY . /app
COPY deploy/northflank/supervisord.conf /etc/supervisor/conf.d/mt5.conf
COPY deploy/northflank/crontab /app/deploy/northflank/crontab
RUN chmod +x deploy/northflank/*.sh

EXPOSE 8501 6080
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/mt5.conf", "-n"]
```

- [ ] **Step 3: Build the image**

Run: `docker build -f Dockerfile.northflank --build-arg MT5_INSTALLER_URL="<broker-B installer url>" -t mt5-northflank:dev .`
Expected: build completes; final layers show MetaTrader5+mt5linux pip-installed under Wine.

- [ ] **Step 4: Verify Wine-side bridge deps (the unknown, confirmed here)**

Run:
```bash
docker run --rm mt5-northflank:dev bash -lc \
  'source /opt/wine-mt5/winpython.env && xvfb-run -a wine "$WINE_PYTHON" -c "import MetaTrader5, mt5linux; print(\"wine deps ok\")"'
```
Expected: `wine deps ok`. If it fails, fix `install_mt5.sh` (paths/flags) and rebuild — this is the verify loop for the one genuinely-unknown step.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile.northflank deploy/northflank/install_mt5.sh
git commit -m "feat(northflank): Wine + MT5 + mt5linux bridge image"
```

---

### Task 5: supervisord + launch wrappers + crontab

**Files:**
- Create: `deploy/northflank/supervisord.conf`
- Create: `deploy/northflank/wait_for_port.sh`
- Create: `deploy/northflank/start_bridge.sh`
- Create: `deploy/northflank/crontab`

**Interfaces:**
- Consumes: the image from Task 4, `config/portfolio.northflank.yaml` from Task 3.
- Produces: a running process tree — bridge on :18812, bot/ticks/dashboard gated behind `wait_for_port.sh 18812`, VNC gated behind `SETUP=1`, weekly calendar via supercronic.

- [ ] **Step 1: Port-wait wrapper**

```bash
# deploy/northflank/wait_for_port.sh
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
```

- [ ] **Step 2: Bridge launcher (terminal + mt5linux server)**

```bash
# deploy/northflank/start_bridge.sh
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
```

- [ ] **Step 3: supervisord config**

```ini
; deploy/northflank/supervisord.conf
[supervisord]
nodaemon=true
logfile=/data/logs/supervisord.log
childlogdir=/data/logs

[program:xvfb]
command=Xvfb :0 -screen 0 1280x900x24
autorestart=true
priority=1

[program:fluxbox]
command=fluxbox
environment=DISPLAY=":0"
autorestart=true
priority=2

; VNC for one-time login — only when SETUP=1 (set autostart via the env-expanded command).
[program:vnc]
command=/bin/bash -c 'if [ -n "$SETUP" ]; then x11vnc -display :0 -forever -nopw -rfbport 5900 & exec websockify --web=/usr/share/novnc 6080 localhost:5900; else sleep infinity; fi'
autorestart=true
priority=5

[program:bridge]
command=/app/deploy/northflank/start_bridge.sh
autorestart=true
startsecs=20
priority=10

[program:portfolio]
command=/app/deploy/northflank/wait_for_port.sh 18812 uv run python scripts/run_portfolio.py --name acctB --portfolio config/portfolio.northflank.yaml
directory=/app
autorestart=true
priority=20
stdout_logfile=/data/logs/acctB_stdout.log
redirect_stderr=true

[program:ticks]
command=/app/deploy/northflank/wait_for_port.sh 18812 uv run python scripts/log_ticks.py --portfolio config/portfolio.northflank.yaml --flush-seconds 60
directory=/app
autorestart=true
priority=20
stdout_logfile=/data/logs/ticks_stdout.log
redirect_stderr=true

[program:dashboard]
command=/app/deploy/northflank/wait_for_port.sh 18812 uv run --with streamlit --with plotly streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501
directory=/app
autorestart=true
priority=25
stdout_logfile=/data/logs/dashboard_stdout.log
redirect_stderr=true

[program:cron]
command=supercronic /app/deploy/northflank/crontab
directory=/app
autorestart=true
priority=30
```

- [ ] **Step 4: Crontab (weekly calendar fetch — replaces the VPS scheduled task)**

```cron
# deploy/northflank/crontab — weekly high-impact USD calendar (Sun 22:00 UTC)
0 22 * * 0 cd /app && uv run python scripts/fetch_calendar.py --impact high --currency USD >> /data/logs/calendar.log 2>&1
```

- [ ] **Step 5: Verify supervisord parses the config (no live MT5 needed)**

Run: `docker run --rm -e SETUP= mt5-northflank:dev supervisord -c /etc/supervisor/conf.d/mt5.conf -t || true`
Then: `docker run --rm mt5-northflank:dev bash -lc 'supervisord -c /etc/supervisor/conf.d/mt5.conf -n & sleep 8; supervisorctl status; kill %1'`
Expected: `xvfb`, `bridge`, `portfolio`, `ticks`, `dashboard`, `cron` listed; `portfolio/ticks/dashboard` in a wait state (bridge not truly logged in yet — that's Task 7). No config parse errors.

- [ ] **Step 6: Commit**

```bash
git add deploy/northflank/supervisord.conf deploy/northflank/wait_for_port.sh deploy/northflank/start_bridge.sh deploy/northflank/crontab
git commit -m "feat(northflank): supervisord topology + launch wrappers + cron"
```

---

### Task 6: Northflank service config

**Files:**
- Create: `northflank.json`

**Interfaces:**
- Produces: a Northflank Combined-service spec building `Dockerfile.northflank` from `deploy/northflank`, with the persistent volume, ports, and secret references. (Importable via `northflank` CLI / API, or used as the source of truth for the UI.)

- [ ] **Step 1: Write `northflank.json`**

```json
{
  "apiVersion": "v1",
  "spec": {
    "kind": "CombinedService",
    "name": "mt5-acct-b",
    "billing": { "deploymentPlan": "nf-compute-20" },
    "deployment": { "instances": 1 },
    "vcsData": {
      "projectUrl": "<git remote url>",
      "projectBranch": "deploy/northflank"
    },
    "buildSettings": {
      "dockerfile": { "buildEngine": "kaniko", "dockerFilePath": "/Dockerfile.northflank", "dockerWorkDir": "/" }
    },
    "ports": [
      { "name": "dashboard", "internalPort": 8501, "public": true,  "protocol": "HTTP" },
      { "name": "vnc",       "internalPort": 6080, "public": false, "protocol": "HTTP" }
    ],
    "runtimeEnvironment": {
      "MT5_BRIDGE": "1",
      "MT5_BRIDGE_HOST": "localhost",
      "MT5_BRIDGE_PORT": "18812",
      "WINEPREFIX": "/data/wine"
    },
    "secretRefs": ["mt5-acct-b-secrets"],
    "volumes": [
      { "name": "mt5-data", "mountPath": "/data", "storageSize": 5120 }
    ]
  }
}
```

- [ ] **Step 2: Document the secret group it references**

Add to the file's repo (in Task 7 runbook) that `mt5-acct-b-secrets` must hold `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`. (Created in the Northflank UI/CLI — never committed.)

- [ ] **Step 3: Validate JSON**

Run: `uv run python -c "import json; json.load(open('northflank.json')); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add northflank.json
git commit -m "feat(northflank): combined-service spec (volume, ports, secrets)"
```

---

### Task 7: Runbook + first-login + verification ladder

**Files:**
- Create: `docs/NORTHFLANK.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the operator runbook — secret group creation, build, `SETUP=1` first-login, symbol finalization (Task 3 Step 2-3), and the 5-step verification ladder from the spec.

- [ ] **Step 1: Write `docs/NORTHFLANK.md`**

Contents (write in full, no placeholders beyond the broker-specific URL/symbols the operator must supply):

```markdown
# Account B on Northflank — deploy & runbook

Self-contained Linux container running account B's full live stack via Wine +
the mt5linux bridge. Sibling of the Azure VPS (`docs/WORKFLOW.md`); **different
broker** — see `config/portfolio.northflank.yaml`.

## One-time setup
1. **Secret group** `mt5-acct-b-secrets` (Northflank UI): `MT5_LOGIN`,
   `MT5_PASSWORD`, `MT5_SERVER` for account B. Never commit these.
2. **Build arg**: set `MT5_INSTALLER_URL` to broker B's MT5 installer download.
3. **Create the service** from `northflank.json` (`northflank service create ...`
   or import in the UI). Attaches the 5 GB `/data` volume.

## First login (Algo Trading ON) — once
1. Redeploy with env `SETUP=1`. noVNC comes up on the `vnc` port (`:6080`).
2. Open the VNC URL → in the MT5 terminal: **File → Login to Trade Account**
   (account B creds) → **Ctrl+E to enable Algo Trading** → dismiss dialogs.
   This writes the login + Algo-Trading state into `/data/wine` (persists).
3. Remove `SETUP` and redeploy. VNC stops; the bot trades.

## Finalize account-B symbols
With the bridge up (post first-login), run `scripts/check_symbols.py` and put the
broker's gold/silver strings into `config/portfolio.northflank.yaml` (Task 3),
commit, redeploy.

## Verification ladder
1. **Bridge up:** `supervisorctl status bridge` = RUNNING; `:18812` open.
2. **Shim parity:** `MT5_BRIDGE=1 uv run pytest tests/test_bridge_smoke.py -v`
   (skips if bridge unreachable; PASS when up).
3. **Observe:** `/data/logs/acctB.jsonl` shows advancing STEP events; lock
   `/data/logs/.acctB.lock` held. Spot-check a quote vs Azure.
4. **Order round-trip (HARD GATE, broker B):** place one tiny manual order
   through the bridge; confirm fill + correct filling mode. Then revert.
5. **Dashboard:** the public `:8501` URL renders.

## Restart / redeploy
Git-push to `deploy/northflank` → Northflank rebuilds + redeploys. Each bot holds
a single-instance lock (`single_instance.acquire_or_exit`), so a relaunch is safe.
The bridge dying makes the bot exit (by design) → supervisord relaunches it with a
fresh rpyc link.

## Drift note
This branch is divergent and must not merge to `main` during the competition
(live Jun 15–27 2026). It is kept additive: the only shared-file change is the
merge-safe gateway backend shim.
```

- [ ] **Step 2: Write the skip-guarded bridge smoke test**

```python
# tests/test_bridge_smoke.py
"""Parity smoke test: the gateway works through the live mt5linux bridge.
Skipped unless MT5_BRIDGE is set AND :18812 is reachable, so it is a no-op in
CI / on the dev mac and a real gate when run inside the container."""
import os
import socket

import pytest


def _bridge_up() -> bool:
    if not os.environ.get("MT5_BRIDGE"):
        return False
    host = os.environ.get("MT5_BRIDGE_HOST", "localhost")
    port = int(os.environ.get("MT5_BRIDGE_PORT", "18812"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _bridge_up(), reason="mt5linux bridge not reachable")


def test_gateway_through_bridge():
    from mt5_trader.live.mt5_gateway import MT5Gateway
    gw = MT5Gateway()
    gw.connect(login=int(os.environ["MT5_LOGIN"]),
               password=os.environ["MT5_PASSWORD"],
               server=os.environ["MT5_SERVER"])
    try:
        assert gw.equity() > 0
    finally:
        gw.shutdown()
```

- [ ] **Step 3: Verify the smoke test skips cleanly off-bridge**

Run: `uv run pytest tests/test_bridge_smoke.py -v`
Expected: SKIPPED (1 skipped) on the dev mac — bridge not reachable.

- [ ] **Step 4: Commit**

```bash
git add docs/NORTHFLANK.md tests/test_bridge_smoke.py
git commit -m "docs(northflank): runbook + skip-guarded bridge smoke test"
```

---

## Self-review notes

- **Spec coverage:** backend shim (T1) · fatal reconnect (T2) · broker-B config + symbols (T3) · Wine/MT5/bridge image (T4) · supervisord topology / first-login VNC / cron (T5) · Northflank service+volume+secrets (T6) · runbook + smoke test (T7). All spec sections mapped.
- **Type consistency:** `mt5_backend` exports `mt5` + `IS_BRIDGE`; gateway imports both (T1, used in T2). `--portfolio config/portfolio.northflank.yaml` used identically in T3/T5.
- **Known verify-step (not a placeholder):** exact `mt5linux` server CLI + Wine MetaTrader5 install path — pinned by T4 Step 4's verify loop. Broker-specific installer URL and symbol strings are operator inputs, resolved by explicit discovery steps (T3 Step 2, T7), not left as TBD.
```
