# Workflow: Research → Backtest → Deploy → Test

The lifecycle every strategy goes through, with the commands and the gate it
must pass to advance. Code flows one way: **Mac (research) → GitHub → VPS
(execution)**. Nothing is edited on the VPS; the VPS only ever runs
`git pull`.

```
[1 Research] → [2 Backtest] → [3 Paper deploy] → [4 Live round] → [5 Review]
     ↑                                                                 │
     └─────────────────────────────────────────────────────────────────┘
```

---

## 1. Research (Mac)

**Goal:** a falsifiable hypothesis with evidence from the historical dump.

```bash
# once, when the dump arrives:
#   - edit config/schema_map.yaml to match the vendor CSV columns
#   - drop CSVs into data/raw/
uv run scripts/ingest_dump.py data/raw          # -> data/processed/<SYMBOL>/*.parquet
```

Explore in a notebook or scratch script using the library directly:

```python
from mt5_trader.data.ingest import load_ticks
from mt5_trader.data.bars import time_bars
from mt5_trader.features.microstructure import with_micro_features
import polars as pl

ticks = with_micro_features(load_ticks("data/processed", "XAUUSD"))
bars = time_bars(ticks, "1m", extra={"ofi": pl.col("ofi").sum()})
```

Standard first checks (see research checklist in `STRATEGY_PLAYBOOK.md`):
spread/cost by hour, OFI predictiveness, session range stats, pair betas.

**Gate to advance:** a stated edge ("OFI z>2 predicts positive 5-min return")
that survives a look at week-by-week splits — not just the full-sample average.

## 2. Backtest (Mac)

**Goal:** the edge survives costs, walk-forward, and parameter wiggle.

Implement as a class in `src/mt5_trader/strategies/` (registry pattern — copy
an existing one). The engine enforces signal-on-close → fill-next-bar.

```bash
uv run pytest                                            # must stay green
uv run scripts/run_backtest.py --strategy <name> --symbol XAUUSD
uv run scripts/run_backtest.py --strategy <name> --symbol XAUUSD --slippage-bps 2  # cost stress
```

Required checks:
- **Walk-forward:** fit parameters on weeks 1–3 of the dump, evaluate on
  week 4 only. In-sample-only performance = cut.
- **Cost stress:** still positive at 2–4× assumed slippage? Competition fills
  are unknown until paper trading.
- **Parameter wiggle:** ±25% on each parameter; a cliff = overfit.
- **Eyeball the equity curve** in `reports/` — one lucky day carrying the
  whole P&L is not an edge.

**Gate to advance:** positive walk-forward P&L after stressed costs, plus a
sane turnover (cost share of gross P&L < ~30%).

## 3. Paper deploy (VPS)

**Goal:** the strategy behaves identically in MT5 reality — fills, symbol
names, lot rounding, server quirks.

```bash
# Mac: ship it
git add -A && git commit && git push
```

```powershell
# VPS: pull and run against the demo / competition paper account
git pull
pytest                                   # green on Windows too
set MT5_LOGIN=... & set MT5_PASSWORD=... & set MT5_SERVER=...
python scripts\run_live.py --strategy <name> --symbol XAUUSD
# pair strategies trade both legs:
python scripts\run_live.py --strategy ratio_mr --symbol XAUUSD --symbol2 XAGUSD --beta 1.0
```

Verify in `logs\live_*.jsonl`:
- `STEP` every minute, no repeating `ERROR`, `RECONNECT_OK` after terminal
  restarts.
- When signals fire: orders in the MT5 terminal, `traded_lots` ≠ 0, position
  matches `target_lots`.
- **Fill quality:** compare actual fill prices vs the bar closes the backtest
  assumed → recalibrate `slippage_bps` in the backtest and re-check gate 2.
- Kill switch drill: set `loss_limit: 0.001` in a throwaway branch of
  `config/risk.yaml`, confirm `KILL_SWITCH` event + flat position, revert.

**Gate to advance:** ≥3 trading sessions of paper logs; realized slippage
within the stressed backtest assumption; zero unexplained errors.

## 4. Live round (VPS, Jun 21–24)

**Goal:** execute the frozen plan; touch nothing without reason.

- Parameters frozen by Jun 20 (committed, tagged: `git tag round1 && git push --tags`).
- Round risk budgets come from `config/risk.yaml` — survive (R1–R2) then
  push (R3). Changing risk = config commit, not code edit.
- Routine per round: confirm runner alive at round start; check logs at
  London open, NY open, and 2h before round end; manual cutoff decisions
  per the tournament-context section of `STRATEGY_PLAYBOOK.md`.
- Emergency stops, in order: Ctrl+C the runner (positions stay open) →
  flatten manually in the MT5 terminal → kill switch already did it for you.

## 5. Review (after each round, ~30 min)

```bash
# pull the VPS logs back for analysis (run on VPS, push artifacts or copy via RDP)
```

- P&L attribution: which strategy, which hours, fills vs expectation.
- Slippage drift: update `slippage_bps`; re-run backtests if it moved a lot.
- Risk config for the next round: equity cushion vs expected cutoff →
  adjust `target_vol`/`max_leverage` in `config/risk.yaml`, commit, pull on VPS.
- Anything broken → fix on Mac → tests → push → pull. Never hotfix on the VPS.

---

## Appendix: driving the VPS over SSH (no RDP needed)

One-time setup already done by `scripts/setup_ssh_azure.sh` (OpenSSH on the VM,
your key, port 22 from your IP, `mt5-vps` alias) plus a read-only GitHub deploy
key on the VPS so `git pull` works headless. Daily commands, all from the Mac:

```bash
ssh mt5-vps                                            # interactive PowerShell on the VPS
ssh mt5-vps "cd Desktop/MT5_Trader; git pull"          # deploy latest code/config
ssh mt5-vps "cd Desktop/MT5_Trader; git log --oneline -1"   # what's deployed?

# monitor the runner
ssh mt5-vps "cd Desktop/MT5_Trader; Get-Content logs\live_XAUUSD_donchian.jsonl -Tail 10"

# or let the watchdog do it (macOS notification + sound on stale log / ERROR / KILL)
uv run scripts/watchdog.py --loop 300

# pull logs home for post-round review
scp "mt5-vps:Desktop/MT5_Trader/logs/*.jsonl" reports/

# restart the runner after a deploy (then relaunch it — see below)
ssh mt5-vps "Stop-Process -Name python -ErrorAction SilentlyContinue"
```

Caveats:
- **MT5 terminal is GUI-only.** It survives RDP disconnects, but to (re)start
  it you need one RDP session: launch terminal, log in, *disconnect* (close the
  window on the Mac side) — never "Sign out", which kills the session.
- **Starting the runner over SSH works** (`python scripts\run_live.py ...`)
  but dies with the SSH session; for unattended runs start it from an RDP
  session (or a scheduled task) so it outlives the connection.
- **Home IP changed?** RDP/SSH stop connecting:
  `bash scripts/provision_azure.sh ip`
- VPS can `git pull` but not push (read-only deploy key, deliberate —
  one-way code flow).

## Rules that keep you out of trouble

1. **One-way code flow.** Mac → GitHub → VPS. The VPS is a dumb executor.
2. **Tests gate every push.** `uv run pytest` before commit; `pytest` after pull.
3. **No lookahead, ever.** If a strategy needs `.shift(-1)`, it's broken.
4. **Costs are a research input,** measured from paper fills, not a hopeful constant.
5. **Risk changes are config diffs** — reviewable, revertible, logged in git.
6. **The audit log is the source of truth.** If it isn't in `logs/*.jsonl`,
   it didn't happen.
