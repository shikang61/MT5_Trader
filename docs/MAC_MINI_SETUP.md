# Mac mini setup — ready-state for a future live cutover

Goal: get the always-on Mac mini (Apple Silicon) ready so that **after the
competition** the live stack can move off the Azure VPS onto the mini with
minimal fuss. Nothing here changes the running VPS — it is prep only.

Two environments live on the mini:

| Env | Runs | Purpose |
|-----|------|---------|
| **Native macOS** | this repo, `uv` | research, backtests, `scripts/watchdog.py` |
| **Win11 ARM VM** (VMware Fusion) | MT5 terminal + this repo | **live trading** (mirror of the VPS) |

The `MetaTrader5` pip package is win32-only and talks to a local MT5 terminal
over a Windows IPC pipe — so live trading must run inside the VM, never on
macOS. (`pyproject.toml` pins it behind `sys_platform == 'win32'`.)

---

## Part 1 — Native macOS env (research)

```bash
# install uv if missing
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone <repo-url> ~/Desktop/MT5_Trader
cd ~/Desktop/MT5_Trader
uv sync --extra dev          # MetaTrader5 is skipped on macOS — expected
uv run pytest                # full suite should pass
```

That is the whole research env. `scripts/watchdog.py` can run here too (it
checks the live logs over ssh — repoint `HOST` once the VM is the live host).

---

## Part 2 — Win11 ARM VM (live) — VMware Fusion

### 2.1 Create the VM

1. Fusion → **New** → install **Windows 11 ARM**. (Fusion 13+ on Apple Silicon
   only runs ARM guests — that is fine, see the x64 note below.)
   - Get the Win11 ARM image via VMware's built-in "Get Windows from Microsoft"
     flow, or a Win11 ARM VHDX/ISO.
2. Give it **≥4 vCPU, ≥8 GB RAM, ≥64 GB disk**. The mini host needs headroom —
   16 GB+ total RAM on the mini is comfortable for host + always-on VM.
3. Install VMware Tools (shared clipboard, stable networking).

### 2.2 CRITICAL — install **x64** Python, not ARM64

Win11 ARM emulates x86/x64 (Prism). The chain must be all-x64:

- MT5 terminal = x64 → runs emulated.
- `MetaTrader5` wheel = win-**x64** CPython extension.
- So install **x64 Python** (the regular `python.org` "Windows installer (64-bit)",
  *not* the ARM64 build). An ARM64 Python cannot load the win-x64 wheel and the
  IPC handshake to the x64 terminal will fail.

Verify inside the VM:
```
python -c "import platform; print(platform.machine())"   # -> AMD64 (good), ARM64 (wrong)
```

### 2.3 Install MT5 terminal

- Download the **competition broker's** MT5 terminal, install, log in to the
  demo/comp account once so the terminal stores the connection.
- Leave the terminal running — the Python API attaches to it.

### 2.4 Repo + venv inside the VM

Keep the **same path and Windows username as the VPS** so `start_all.bat` works
unchanged — it hardcodes `C:\Users\shikang\Desktop\MT5_Trader`. Either create a
Windows user `shikang`, or edit the `cd /d` line in `scripts/start_all.bat`
(and `start_passive.bat`).

```
git clone <repo-url> C:\Users\shikang\Desktop\MT5_Trader
cd C:\Users\shikang\Desktop\MT5_Trader
uv venv
uv sync                      # MetaTrader5 installs here (win32)
mkdir logs                   # gitignored; scripts write JSONL heartbeats here
copy .env.example .env       # then fill MT5_LOGIN / MT5_PASSWORD / MT5_SERVER
```

`.env` is gitignored. For real money later, prefer Windows Credential
Manager / DPAPI over plaintext (noted in `.env.example`).

### 2.5 Smoke test (still demo — safe)

```
.venv\Scripts\python.exe -c "from mt5_trader.live.mt5_gateway import MT5Gateway; g=MT5Gateway(); g.connect(login=..., password='...', server='...'); print(g.equity()); g.shutdown()"
```

Equity prints → IPC over emulation works. This is the one unknown about the
ARM/emulation path; confirm it early. Then a full demo run:

```
scripts\start_all.bat        # launches the portfolio bot (magic 1001)
```

Verify `logs\portfolio.jsonl` is updating (heartbeat ~60s).

---

## Part 3 — Always-on plumbing

So the stack survives reboots / runs unattended:

**Mac mini (host)**
- Disable sleep: `sudo pmset -a sleep 0 disablesleep 1`
- macOS auto-login (System Settings → Users & Groups → auto-login).
- Fusion → VM Settings → **Start automatically** when Fusion launches; add
  Fusion to Login Items so boot → Fusion → VM.

**Windows (guest)**
- Auto-login (`netplwiz`, uncheck "must enter password").
- Auto-start bots at logon: put a shortcut to `start_all.bat` in
  `shell:startup`, or a Task Scheduler task "at log on".
- Re-create the **weekly calendar-fetch task** (S4U, see
  `memory/vps-runtime-setup`): after each fetch, restart the bot so blackout
  events reload.
- Re-create the **weekday daily-report task** (`MT5 Daily Report`, S4U,
  21:10 UTC Mon-Fri): `schtasks /create /tn "MT5 Daily Report" /tr "cmd /c cd
  /d C:\Users\shikang\Desktop\MT5_Trader && .venv\Scripts\python.exe
  scripts\daily_report.py >> logs\daily_report.log 2>&1" /sc WEEKLY /d
  MON,TUE,WED,THU,FRI /st 21:10 /ru shikang /rl LIMITED /np /f`. Sends the
  per-strategy performance summary via Telegram (`scripts/daily_report.py`);
  needs `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env`.
- Re-create the **watchdog task** (`MT5 Watchdog`, S4U, every 5 min): runs
  `scripts\vps_watchdog.py >> logs\watchdog.log` from the repo dir — pages
  Telegram when a runner log goes stale (loop dead) or logs ERROR/KILL_SWITCH
  events; all-clear on recovery. Register via PowerShell
  `Register-ScheduledTask` with a `-Once` trigger repeating every 5 min,
  principal S4U (setx `TELEGRAM_*` vars are visible to S4U — verified
  2026-07-06; plain `cmd` paths must use backslashes).

---

## Part 4 — Cutover (post Jun 27, NOT before)

⚠️ **Never run the VPS bots and the mini bots against the same account/magic at
the same time.** The single-instance lock (`single_instance.acquire_or_exit`)
is per-machine — it will NOT stop the VPS and the mini from both trading magic
1001 and double-positioning. Cutover sequence:

1. Stop the VPS bots (and confirm flat / intended state).
2. Start the mini bots; verify `logs\portfolio.jsonl` heartbeating.
3. Repoint `scripts/watchdog.py` `HOST` (or run it natively against local logs)
   so monitoring follows the live host.

---

## Ready-state checklist (do now)

- [ ] Native macOS env: `uv run pytest` green on the mini.
- [ ] Win11 ARM VM created, VMware Tools installed.
- [ ] x64 Python verified (`platform.machine()` → `AMD64`).
- [ ] MT5 terminal installed + demo login saved.
- [ ] Repo cloned to `C:\Users\shikang\Desktop\MT5_Trader`, `uv sync` ok.
- [ ] `.env` filled (demo creds), `logs\` exists.
- [ ] Smoke test: `MT5Gateway().equity()` prints under emulation.
- [ ] `start_all.bat` runs a clean demo session, logs heartbeat.
- [ ] Always-on plumbing (Part 3) configured + survives a reboot.

When every box is checked, the mini is ready and cutover is just Part 4.
