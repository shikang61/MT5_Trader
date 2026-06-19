# Northflank deployment — run account B on Linux containers

**Date:** 2026-06-19
**Branch:** `deploy/northflank` (long-lived, divergent from `main`)
**Goal:** Run the full live stack (portfolio bot + tick logger + calendar fetch +
dashboard) for a **second MT5 account (account B)** on Northflank, in a
self-contained Linux container, independent of the Azure Windows VPS.

## Context & constraints

- The Model to Market competition is **live (Jun 15–27 2026)** on the Azure VPS.
  Touching `main` risks the running deploy, so this work lives on a divergent
  `deploy/northflank` branch and does **not** merge to `main` during the comp.
- To keep an eventual merge clean, the branch is kept **~99% additive**: new
  files only, except one merge-safe one-line edit to the gateway import.
- `MetaTrader5` (pip) is **win32-only** and a **process-global singleton** (one
  process ↔ one terminal ↔ one account — see `docs/MULTI_ACCOUNT.md`). On Linux
  the terminal runs under **Wine** and Python reaches it via the **`mt5linux`
  RPC bridge**.
- **Account B is on a different broker** from the comp/Azure account. Different
  terminal build, symbol names, contract specs, and likely filling mode.

## Why divergent branch (decision)

Chosen over merge-to-main because the comp is live and `main` drives the Azure
deploy. Drift — the pain behind commit `e7e4fc6` ("sync VPS launchers — capture
drift") — is mitigated by keeping the branch additive: the only shared-file edit
(the gateway import) is a no-op on Windows, so it could merge back harmlessly if
desired post-comp.

## Architecture — single all-in-one container

Northflank persistent volumes attach to **one deployment**; two services cannot
share a filesystem. The full stack reads/writes shared logs/data/Wine-prefix, so
everything runs in **one container, one volume** — mirroring the Azure one-box
model. `supervisord` manages all processes.

```
Xvfb :0  (virtual display — MT5 is a GUI app even headless)
  └─ wine terminal64.exe /portable        (account B terminal; profile on volume)
       └─ mt5linux RPC server :18812       (Wine-python side; has real MetaTrader5)
native Linux python (rpyc → localhost:18812):
  ├─ run_portfolio.py --name acctB --portfolio config/portfolio.northflank.yaml
  ├─ log_ticks.py                          (read-only; shares the session)
  └─ streamlit dashboard :8501             (read-only)
supercronic crontab:
  └─ weekly fetch_calendar.py              (replaces the VPS scheduled task)
```

Tradeoff accepted: no process isolation. `supervisord` restarts any crashed
process; the bot reconnects (see Failure modes).

**Shared-session note:** the four mt5-touching paths share **one** terminal
session (mt5 = one account per terminal). The bot trades; tick logger + dashboard
only read. `mt5linux` serializes RPC calls server-side, so read-clients sharing
the trader's session is workable — this is the **main risk to validate** (see
Testing). Not pre-optimized.

## Component 1 — gateway backend shim

**New file `src/mt5_trader/live/mt5_backend.py`** — selects the backend, exports `mt5`:

```python
import os
try:
    import MetaTrader5 as mt5            # Windows: native win32 package
except ImportError:                      # Linux/Northflank: mt5linux RPC bridge
    try:
        from mt5linux import MetaTrader5
        mt5 = MetaTrader5(
            host=os.environ.get("MT5_BRIDGE_HOST", "localhost"),
            port=int(os.environ.get("MT5_BRIDGE_PORT", "18812")),
        )
    except (ImportError, ConnectionError):
        mt5 = None
```

**One-line edit to `src/mt5_trader/live/mt5_gateway.py`** — replace its local
`try: import MetaTrader5 as mt5 / except ImportError: mt5 = None` block with:

```python
from .mt5_backend import mt5
```

Properties:
- **Merge-safe.** On Windows the first `import` wins → behavior byte-identical to
  today. Could merge to `main` post-comp without effect.
- **Same API surface.** `mt5linux`'s object proxies the real module — `initialize`,
  `TIMEFRAME_*`, `history_deals_get`, `symbol_info_tick`, etc. forward over RPC.
  Gateway methods unchanged.
- **`mt5linux` is NOT a pyproject dependency** (stays win32-clean). Installed in
  the Dockerfile (Linux image) only.
- **Constraint:** the backend constructs the RPC connection at import → the
  Wine-side server must be up first. Satisfied by supervisord priority + a
  `wait-for :18812` wrapper before the bot starts (Component 2).

`broker_pnl.py` is mt5-free at import (pure function on `deals`) — no change.
`scripts/check_symbols.py` is a dev script, out of the live loop — not touched.

## Component 2 — process supervision (`supervisord`)

| pri | process | notes |
|----|---------|-------|
| 1 | `Xvfb :0` | virtual display |
| 5 | `x11vnc` + `noVNC` (:6080) | **only when `SETUP=1`** — one-time login; else not started |
| 10 | `wine terminal64.exe /portable` | terminal install + logged-in profile on volume |
| 15 | `mt5linux` RPC server (`0.0.0.0:18812`) | Wine-python side |
| 20 | `wait-for :18812` → `run_portfolio.py --name acctB --portfolio config/portfolio.northflank.yaml` | bot; creds from secrets |
| 20 | `wait-for :18812` → `log_ticks.py` | read-only |
| 25 | `streamlit` dashboard (:8501) | read-only |
| — | `supercronic` crontab → weekly `fetch_calendar.py` | replaces VPS scheduled task |

The `wait-for :18812` wrapper enforces the Component 1 ordering constraint.

**Verify at implementation:** exact `mt5linux` server CLI (it wraps a Wine
python path). Marked a verify-step, not assumed.

## Component 3 — first-login flow

Two GUI-only requirements: log account B into the terminal, and **enable Algo
Trading** (`docs/MULTI_ACCOUNT.md`: "the API cannot send orders without it").
`initialize(login,password,server)` does login headlessly, but Algo-Trading-ON +
first-run "allow" dialogs are GUI. So, one-time:

1. Deploy with `SETUP=1` → noVNC at `:6080`.
2. Browser → VNC → log into account B, **Ctrl+E (Algo Trading ON)**, dismiss dialogs.
3. State written to the terminal profile **on the persistent volume** → survives redeploys.
4. Redeploy without `SETUP` → VNC gone, bot trades. Mirrors RDP-once on Azure.

## Component 4 — secrets, persistence, Northflank config

**Secrets** (Northflank Secret Group → env vars, never committed):
`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (read via `os.environ` in
`run_portfolio.py` — no `.env` file), plus `MT5_BRIDGE_HOST=localhost`,
`MT5_BRIDGE_PORT=18812`.

**Persistent volume** mounted at `/data`:
```
/data/wine   → WINEPREFIX (terminal install + profile + Algo-Trading state)
/data/mt5    → MT5 portable data dir
/data/logs   → logs/<name>.jsonl  (bind/symlink from /app/logs)
/data/data   → market/calendar parquet (bind/symlink from /app/data)
```

**Northflank service:** Combined service — builds `Dockerfile.northflank` from
the `deploy/northflank` branch, deploys, attaches the volume. Git-push →
rebuild → redeploy. Ports: `8501` public (dashboard), `6080` restricted/temp
(VNC). Resources: ~2 GB RAM / 1 vCPU; tune after first run.

**`Dockerfile.northflank`:** ubuntu → winehq-stable → Xvfb/x11vnc/noVNC →
silent-install **account B's broker MT5 terminal** under Wine → `uv` + project
install (native Linux python) → `pip install mt5linux` → supervisord conf +
launch scripts.

## Broker-B differences (config + verify, not architecture)

1. **Terminal build:** image bakes **B-broker's own MT5 installer** (not the
   comp broker's).
2. **Symbol names:** B-broker's gold may be `GOLD`/`XAUUSD.r`/etc. Account B gets
   its **own** `config/portfolio.northflank.yaml` with B's symbol strings,
   launched via `--portfolio`. Calendar/blackout are symbol-agnostic.
3. **Filling mode / contract specs / stop-freeze levels:** FOK vs IOC, min stop
   distance, digits vary by broker. SL/TP rounding is already handled (commit
   `9d86681`); the **filling mode is the likely break** → order round-trip is a
   hard gate (Testing step 4).
4. **Server timezone / spread:** bar timestamps + spread filters — spot-check vs config.

## Failure modes

- **Wine terminal crash / auto-update.** `supervisord` restarts terminal + bridge.
  But the bot's `reconnect()` does `mt5.shutdown()+initialize()` over a **dead
  rpyc socket** → fails. So on Linux, treat **bridge-connection-loss as
  fatal-to-process** (env-gated) → bot exits → supervisord restarts it → fresh
  import rebuilds the rpyc connection + re-`connect()`. The single-instance lock
  releases on exit, so no dup. Windows path keeps using `reconnect()` unchanged.
- **Shared-session contention.** `mt5linux` serializes RPC server-side; read
  clients stay low-frequency. Validate in testing; split tick logger to its own
  terminal only if it bites.
- **Order latency under Wine.** Higher than native Windows — fine for M1/M5 books
  (HF research is dead).
- **Restart safety.** Unchanged & solid: `realized_pnl()` re-derived from broker
  history (restart-safe); single-instance lock prevents dupes.
- **Security.** Creds only in Northflank secret group → env; never in image
  layers or git. Volume holds the logged-in profile — private volume only.

## Testing / verification ladder

1. **Build image locally**, exec in, VNC-login a demo account → assert
   `mt5.account_info().login == B` via the bridge.
2. **Shim parity smoke test** (key gate): run the gateway through the bridge —
   `connect()`, `equity()`, M1 `rates`, `realized_pnl()` — assert no errors, sane
   values. Add as a pytest that **skips unless `MT5_BRIDGE` reachable**.
3. **Observe**: run bot on demo B → `logs/acctB.jsonl` STEP events advancing +
   lock held; spot-check quotes vs Azure (same market view).
4. **Order round-trip (hard gate, broker-B):** one tiny manual order via the
   bridge → confirms fill + Algo-Trading ON + filling mode correct. Then revert.
5. **Northflank**: push branch → build → deploy `SETUP=1` → VNC login → redeploy
   → STEP events in Northflank logs + dashboard on `:8501`.

Existing `broker_pnl` pure-function tests stay green. Only new test = the
skip-guarded bridge smoke test.

## File manifest (all new except the one merge-safe gateway edit)

| file | new/edit | purpose |
|------|----------|---------|
| `src/mt5_trader/live/mt5_backend.py` | new | backend selector |
| `src/mt5_trader/live/mt5_gateway.py` | **edit (1 line)** | import from backend (merge-safe) |
| `config/portfolio.northflank.yaml` | new | account B symbols/books |
| `Dockerfile.northflank` | new | Wine + MT5 + bridge + project image |
| `deploy/northflank/supervisord.conf` | new | process supervision |
| `deploy/northflank/launch_*.sh`, `wait_for_port.sh` | new | launch wrappers |
| `deploy/northflank/crontab` | new | weekly calendar fetch |
| `northflank.json` (or template) | new | service + volume + ports + secrets |
| `tests/test_bridge_smoke.py` | new | skip-guarded parity test |
| `docs/NORTHFLANK.md` | new | deploy/setup/first-login runbook |

## Out of scope

- Merging to `main` (post-comp decision).
- Account B strategy *changes* — same books, B-broker symbols (this is leverage,
  not diversification; correlated drawdowns — see `docs/MULTI_ACCOUNT.md`).
- Splitting tick logger to its own session (only if contention testing demands).
