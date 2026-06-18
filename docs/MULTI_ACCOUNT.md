# Running the strategy on multiple accounts

Drive the same portfolio strategy on several MT5 accounts at once (e.g. the
competition demo + a real-money account, or several real accounts).

## Why it takes one terminal + one process per account

The `MetaTrader5` Python module is a **process-global singleton**: a process
calls `mt5.initialize()` once and is then bound to **one terminal logged into one
account**. You cannot switch `login()` to serve a second account without
disrupting the first. So "N accounts at once" = **N MT5 terminals + N bot
processes**, each terminal logged into its own account, each process attached to
its own terminal via `mt5.initialize(path=...)`.

This is the same multi-process pattern the VPS already uses for the 3 bots
(`MT5Portfolio` / `MT5Passive` / `MT5TickLogger`), extended to N *accounts*
instead of N *strategies on one account*.

> **This is leverage, not diversification.** The same signals on N accounts is
> N × the *same* bet on the same instrument — drawdowns are perfectly
> correlated. A bad week hits every account together.

## Code support (already in `run_portfolio.py`)

| flag | purpose | default |
|------|---------|---------|
| `--name` | instance name — keys the single-instance lock **and** the log file (`logs/<name>.jsonl`) so accounts don't collide | `portfolio` |
| `--terminal` | path to *this* account's `terminal64.exe` (passed to `mt5.initialize(path=...)`) | default terminal |
| `--env` | per-account `.env` with `MT5_LOGIN/PASSWORD/SERVER` (loaded with `override=True`, so it beats the VPS's ambient user env vars) | ambient env |

Position sizing needs no change — `risk.size()` reads each account's own
`gateway.equity()`, so every account sizes to its own balance.

```
run_portfolio.py --name acctA --terminal "C:\Program Files\MetaTrader 5\terminal64.exe" --env A.env
run_portfolio.py --name acctB --terminal "C:\MT5-B\terminal64.exe"                      --env B.env
```

## The critical gotcha — launch portable terminals yourself

MT5's `/portable` flag makes a terminal keep all its data (config, logs, **and
the logged-in account**) *inside its own install folder* instead of the shared
`%APPDATA%\MetaQuotes\Terminal\<hash>`. That is what isolates two terminals.

**But `mt5.initialize(path=...)` does NOT pass `/portable`.** If it has to launch
the terminal itself, it starts it in normal mode → it shares `%APPDATA%` with the
default terminal → collision. So **launch each portable terminal yourself first**
(with `/portable`, logged in), then the bot merely *attaches* to the
already-running instance. The launcher `.bat` below does this.

## Setup for one extra account (account B) — on the VPS over RDP

The existing account (A) is the default install:
`C:\Program Files\MetaTrader 5\terminal64.exe` (data in
`%APPDATA%\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075`).

1. **Copy the install** to a non–Program-Files path (avoids UAC write issues):
   ```powershell
   Copy-Item "C:\Program Files\MetaTrader 5" "C:\MT5-B" -Recurse
   ```
   *(Different broker for B? Skip the copy — run that broker's own MT5 installer
   into `C:\MT5-B` instead; each broker ships its own terminal build.)*

2. **Launch B in portable mode** (first launch builds its own data tree):
   ```
   C:\MT5-B\terminal64.exe /portable
   ```

3. **In terminal B:** File → Login to Trade Account → account B's
   login / password / server. Turn **AutoTrading ON** (Ctrl+E) — the API cannot
   send orders without it. B now auto-logs-in on every `/portable` relaunch.

4. **Per-account creds** — `C:\Users\shikang\Desktop\MT5_Trader\B.env`
   (gitignored — never commit credentials):
   ```
   MT5_LOGIN=<B login>
   MT5_PASSWORD=<B password>
   MT5_SERVER=<B server>
   ```

5. **Durable launcher** — `scripts\start_acctB.bat`:
   ```bat
   @echo off
   REM Account B: launch its portable terminal, wait for connect, then the bot.
   start "" /b "C:\MT5-B\terminal64.exe" /portable
   timeout /t 15
   cd /d C:\Users\shikang\Desktop\MT5_Trader
   .venv\Scripts\python.exe scripts\run_portfolio.py --name acctB --terminal "C:\MT5-B\terminal64.exe" --env B.env >> logs\acctB_stdout.log 2>&1
   ```

6. **Scheduled task** — clone `MT5Portfolio` → point it at `start_acctB.bat`,
   **LogonType = InteractiveToken** (so it runs in the logged-on session where
   terminal B's GUI lives). Restart it the same way as the portfolio bot
   (Stop/Start-ScheduledTask — see `docs/WORKFLOW.md` §7).

## Two must-dos once two terminals run

- **Pin account A's bot too:** add
  `--terminal "C:\Program Files\MetaTrader 5\terminal64.exe"` to A's launch.
  With two terminals up, a no-path `mt5.initialize()` is ambiguous and may attach
  to the wrong one. (Update `start_portfolio.bat` accordingly.)
- **AutoTrading ON in every terminal**, and budget **~150–300 MB RAM + CPU per
  terminal**. A handful is fine on the current VM; many → a bigger VM or split
  across VPSs.

## Verify B

```python
import MetaTrader5 as mt5
mt5.initialize(path=r"C:\MT5-B\terminal64.exe", login=<B>, password="...", server="...")
print(mt5.account_info().login)   # must equal account B
```

Then confirm `logs\acctB.jsonl` is advancing with `STEP` events, and the
`logs\.acctB.lock` exists (single-instance lock held).

## Related

- `docs/WORKFLOW.md` — deploy / SSH+schtasks restart / one-way code flow.
- `scripts/run_portfolio.py` — the `--name/--terminal/--env` flags.
- `src/mt5_trader/live/mt5_gateway.py` — `connect(..., path=...)`.
