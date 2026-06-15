# Workflow: the alpha lifecycle

How a trading idea becomes a live, monitored book — modelled on a modern quant
hedge fund, scaled to a one-person desk. Two roles share this repo:

- **Quant Researcher (QR)** — finds and validates *alpha*: hypothesis, signal
  diagnostics, backtest, portfolio fit. Output: a registered strategy + evidence.
- **Quant Developer (QD)** — owns the *platform*: data, backtester, execution,
  risk, deployment, monitoring. Output: the rails QR research runs on, unchanged
  from research to production.

Two principles make the rest work:

1. **Research–production parity.** A strategy is one `signal(bars) -> positions`
   object (`strategies/base.py`); the *same* code runs in the backtester and
   live. No reimplementation, so no research-vs-live drift.
2. **One-way code flow.** Mac (research) → GitHub → VPS (execution). The VPS only
   `git pull`s; nothing is edited there. Code, config, and data are versioned;
   the JSONL audit log is the source of truth.

```
0 Data ─► 1 Hypothesis ─► 2 Signal research ─► 3 Backtest/validation ─►
4 Portfolio construction ─► 5 Risk ─► 6 Paper ─► 7 Deploy ─► 8 Monitor/attribute ─► 9 Review
        ▲                                                                              │
        └──────────────────────── retire decayed alpha, iterate ───────────────────────┘
```

Each stage below: **who** drives it, **what** in this repo, and the **gate** to
advance. Items marked *(adopt)* are standard HF practice not yet tooled here —
do them in a notebook for now, promote to a script when they earn it.

---

## 0. Data & research environment — *QD builds, QR uses*

A clean, point-in-time dataset is the foundation; a leak here invalidates
everything downstream.

```bash
# once per dump: map vendor columns, drop CSVs, ingest
#   edit config/schema_map.yaml ; cp dump/*.csv data/raw/
uv run scripts/ingest_dump.py data/raw      # -> data/processed/<SYMBOL>/*.parquet
uv run scripts/check_symbols.py             # broker symbol names match config
```

- `data/` — `schema.py` (canonical tick/depth schema), `ingest.py`
  (vendor-agnostic → parquet), `bars.py` (`time_bars`). See `data/README.md`.
- **No survivorship / no lookahead** by construction: ticks are immutable, bars
  are built causally, features use only past data.
- *(adopt)* Record dataset provenance + a hash with each research run so a
  backtest is reproducible from data + code + config alone.

**Gate:** loader returns the expected symbols/date range; spread and session
coverage look sane (no gaps masquerading as quiet markets).

## 1. Hypothesis — *QR*

**Economic prior before statistics.** State *why* an edge should exist and *who
is on the other side* before touching a backtest. Data-mined edges with no
mechanism do not survive out-of-sample.

```python
from mt5_trader.data.ingest import load_ticks
from mt5_trader.data.bars import time_bars
from mt5_trader.features.microstructure import with_micro_features
import polars as pl

ticks = with_micro_features(load_ticks("data/processed", "XAUUSD"))
bars  = time_bars(ticks, "5m", extra={"ofi": pl.col("ofi").sum()})
```

- **Trial log (tooled).** Every hypothesis/parameter family you test counts
  toward multiple-testing risk (Stage 3 deflation). `diagnose`/`validate` append
  to `research/trials.jsonl` automatically (`pipeline/trials.py`); the count
  feeds the deflated-Sharpe haircut.

**Gate:** a falsifiable statement with a mechanism — e.g. "post-sweep snapback:
stop-runs past the prior-day extreme over-extend and revert." Narrative per book
lives in `docs/STRATEGY_PLAYBOOK.md`.

## 2. Signal research — *QR*

Characterise the raw signal *before* wrapping it in a strategy.

- Build features from `features/` (`technicals`: ema/atr/rsi/zscore/realized_vol/
  sessions; `microstructure`: microprice, L1/L5 imbalance, OFI, tick signs).
- **Single-signal diagnostics (tooled):** `uv run scripts/strategy.py diagnose
  <name>` → **IC / rank-IC** vs forward return, **signal decay / half-life**
  (how many bars the edge lasts → sets holding period), **turnover**, and a
  **by-regime** slice (`pipeline/diagnostics.py`; regimes from
  `features/regime.py`). *(adopt)* breadth, capacity, and by-session slicing
  remain notebook work.

**Gate:** predictive stat that holds **week-by-week**, not just full-sample —
and a holding period consistent with the measured decay.

## 3. Backtest & validation — *QR + QD*

**Goal:** the edge survives costs, out-of-sample, and the fact that you tried
many things.

```bash
uv run scripts/run_backtest.py --strategy <name> --symbol XAUUSD
uv run scripts/run_backtest.py --strategy <name> --symbol XAUUSD --slippage-bps 2  # cost stress
uv run scripts/validate_books.py            # automated gate for every deployed book
```

`validate_books.py` is the gate-2 harness: per book it reports week-by-week
return/Sharpe, cost stress at 0.5/1/2/4× slippage, and ±25% parameter wiggle.
Engine (`backtest/engine.py`) is vectorized with a strict **signal-on-close,
fill-next-bar** convention — no lookahead by construction, enforced by
`tests/test_engine.py::test_no_lookahead`. Cost model = per-bar spread + slippage.

Required checks:
- **Walk-forward / out-of-sample.** Fit on early weeks, evaluate on held-out
  weeks. In-sample-only performance = cut.
- **Cost stress.** Still positive at 2–4× slippage? Comp fills are unknown until
  paper. Cost share of gross P&L < ~30%.
- **Parameter wiggle.** ±25% per param; a cliff = overfit, a plateau = robust.
- **Eyeball the curve** (`reports/`): one lucky day is not an edge.
- **Out-of-sample + deflated Sharpe (tooled):** `uv run scripts/strategy.py
  validate <name>` runs week-by-week, cost-stress, param-wiggle, a held-out OOS
  split, and a **deflated-Sharpe haircut** from the Stage-1 trial count
  (`pipeline/validate.py`). *(adopt)* full **purged + embargoed CV** with
  per-window re-fit remains notebook work — the OOS gate evaluates fixed params
  on held-out weeks, it does not re-fit per fold.

**Gate:** positive walk-forward P&L after stressed costs, sane turnover, and a
Sharpe that survives the multiple-testing haircut.

## 4. Portfolio construction — *QR + QD*

Books are combined, not run in isolation — the portfolio's risk-adjusted return
is what matters, not any single alpha.

- `config/portfolio.yaml`: each book's `weight` = share of the risk budget
  (Σ≈1); pairs set `symbol2`/`beta`. Same-symbol targets are **netted** before
  ordering (`scripts/run_portfolio.py`).
- **Vol-target sizing** scales each book toward a volatility target so weights
  mean risk, not notional (`risk/manager.py`; per-book `realized_vol` and
  `weighted_frac` appear in the `STEP` log).
- **Decorrelation > raw return.** Weight by OOS performance and *correlation* —
  e.g. `ratio_mr` (XAU/XAG) is held at a small weight specifically for its
  −0.13 correlation to the donchian anchor, not its standalone P&L.

**Gate:** adding the book improves portfolio Sharpe / drawdown (or adds genuine
diversification), not just gross return.

## 5. Risk — *QD*

Risk posture is **config, not code** — reviewable and revertible per round.

- `config/risk.yaml`: vol target, leverage cap, **loss kill switch** (drawdown
  breach → flatten all → halt; path verified by `tests/test_kill_flatten.py`).
- Per-trade `sl_atr_mult` / `tp_atr_mult` exist in the runner (code default
  `sl_atr_mult=3.0`) but are **OFF in `portfolio.yaml` (`0.0`)**: backtests showed
  a trailing stop bled every book on normal pullbacks. The portfolio-level kill
  switch — plus the peak-equity trailing kill — is the live safety net.
- **Event blackout** (`config/risk.yaml` → `blackout`, `features/calendar.py`):
  forces flat around scheduled high-impact releases (default high-impact USD).
  Same `blackout_mask` primitive in backtest and live (`RiskManager.in_blackout`).
  Gate before enabling: `uv run scripts/eval_blackout.py` (needs a populated
  `data/calendar/`). Off until then.

**Gate:** kill-switch drill passes (see Stage 7); limits committed to git.

## 6. Paper / shadow trading — *QD*

**Goal:** identical behaviour in MT5 reality — fills, symbol names, lot rounding,
server quirks.

Deploy to the VPS (Stage 7), let it run, then verify in `logs/portfolio.jsonl`:
- `STEP` every bar; no repeating `ERROR`; `RECONNECT_OK` after terminal restarts.
- When a signal fires: order in the MT5 terminal, `traded_lots` ≠ 0, net
  position matches `target_lots` (books netted per symbol).
- **Fill quality:** compare actual fills vs the bar closes the backtest assumed →
  recalibrate `slippage_bps` and re-run Stage 3.

**Gate:** ≥3 sessions of paper logs; realized slippage within the stressed
backtest assumption; zero unexplained errors.

## 7. Production deploy — *QD*

```bash
# Mac: tests gate the push
uv run pytest && git add -A && git commit && git push

# VPS: pull, test, restart the single deployment unit
ssh mt5-vps "cd Desktop/MT5_Trader; git pull; pytest -q"
ssh mt5-vps "Stop-Process -Name python -ErrorAction SilentlyContinue"
ssh mt5-vps "schtasks /run /tn MT5Portfolio"     # run_portfolio.py, detached
bash scripts/fetch_logs.sh tail                  # confirm the book in STEP events
```

The **portfolio runner is the single deployment unit**: all books in one
process, risk split by weight, same-symbol targets netted, one kill switch.
(`run_live.py` runs a single book to `logs/live.jsonl` for isolated tests — never
run both on overlapping symbols.) Credentials live in VPS user env vars
(`setx MT5_LOGIN ...`). **Reconcile, don't order:** the runner trades the delta
to a target, so restarts are idempotent and manual intervention is safe.

Kill-switch drill: set `loss_limit: 0.001` on a throwaway branch of
`config/risk.yaml`, confirm a `KILL_SWITCH` event + flat position, revert.

## 8. Monitoring & attribution — *QR + QD*

```bash
uv run scripts/watchdog.py --loop 300     # macOS alert on stale log / ERROR / KILL
bash scripts/fetch_logs.sh                # pull logs home
uv run scripts/pnl_report.py              # per-book P&L attribution
```

- **Attribution** (`backtest/attribution.py`): which book, which hours, fills vs
  expectation — from the `portfolio.jsonl` `STEP`/fill events.
- **Slippage drift:** if realized fills moved, update `slippage_bps` and re-gate.
- *(adopt)* **Live signal-decay monitoring:** track realized vs backtested edge
  per book; a book whose live IC fades is a retire candidate.

## 9. Review & iterate — *QR*

After each round (~30 min): P&L attribution, slippage update, next-round risk
config (`target_vol`/`max_leverage` → commit → pull). Log what you learned in
`docs/LESSONS.md`. **Retire decayed alpha** rather than nursing it. Anything
broken → fix on Mac → tests → push → pull. Never hotfix the VPS.

**Strategy stash.** A candidate that fails `validate` but has a real mechanism
goes to the stash (`research/stash.yaml`, managed by `scripts/strategy.py stash`)
rather than the bin — it records which gate fell short and a concrete
improvement idea, so you can rework and re-validate it instead of forgetting it.
`strategy.py list` shows the whole board (deployed / stashed / candidate).
Promotion back to live is gated: `strategy.py promote` refuses anything whose
latest `validate` is not PASS.

Round cadence (freeze → live round → review) and exact competition dates:
`docs/COMPETITION_PLAN.md`. Freeze = commit + tag (`git tag round1 && git push --tags`).

---

## Adding a new strategy (QR quickstart)

Driven by `scripts/strategy.py`: `new` scaffolds the file, `diagnose`/`validate`
gate it, `stash add` parks a sub-par idea with improvement notes, and `promote`
deploys a PASS into `portfolio.yaml`. The steps it automates:

1. `uv run scripts/strategy.py new <name>` (or `cp strategy_template.py
   src/mt5_trader/strategies/<name>.py`) — the template docstring is the contract.
2. Rename the class, set a unique `name`, declare params as dataclass fields.
3. Implement `signal(bars) -> np.ndarray` in `[-1, 1]`. Decided on bar `t`'s
   close, filled next bar. Rolling windows and `.shift(1)` fine; `.shift(-1)` =
   lookahead = broken.
4. Uncomment `@register`; add `from . import <name>` to `strategies/__init__.py`.
5. `uv run pytest` — the registry test auto-covers it (length, NaN, `[-1,1]`).
6. Run Stages 2–3 (diagnostics → backtest → `validate_books.py`).

**Where to read the code:** `docs/ARCHITECTURE.md` (the two-box design + how the
layers connect) → each folder's `README.md` (file-by-file) → reference books
called out in the template (`momentum_breakout.py` state machine,
`session_vol.py` time-of-day, `orderflow_imbalance.py` feature z-score,
`meanrev_pairs.py` two-leg spread).

---

## Appendix: driving the VPS over SSH (no RDP needed)

One-time setup done by `scripts/setup_ssh_azure.sh` (OpenSSH on the VM, your key,
port 22 from your IP, `mt5-vps` alias) + a read-only GitHub deploy key. Daily
commands, all from the Mac:

```bash
ssh mt5-vps                                              # interactive PowerShell
ssh mt5-vps "cd Desktop/MT5_Trader; git pull"            # deploy latest code/config
ssh mt5-vps "cd Desktop/MT5_Trader; git log --oneline -1"   # what's deployed?
ssh mt5-vps "Get-Content Desktop\MT5_Trader\logs\portfolio.jsonl -Tail 10"  # monitor
scp "mt5-vps:Desktop/MT5_Trader/logs/*.jsonl" reports/   # pull logs home
ssh mt5-vps "Stop-Process -Name python -ErrorAction SilentlyContinue"  # stop runner
```

Caveats:
- **MT5 terminal is GUI-only.** It survives RDP disconnects, but to (re)start it
  you need one RDP session: launch, log in, *disconnect* (close the window) —
  never "Sign out", which kills the session.
- **Starting the runner over SSH works** but dies with the SSH session; for
  unattended runs use the scheduled task so it outlives the connection.
- **Home IP changed?** RDP/SSH stop connecting: `bash scripts/provision_azure.sh ip`.
- VPS can `git pull` but not push (read-only deploy key — one-way flow).

## Rules that keep you out of trouble

1. **One-way code flow.** Mac → GitHub → VPS. The VPS is a dumb executor.
2. **Research–production parity.** Same `signal()` in backtest and live, always.
3. **Tests gate every push.** `pytest` before commit and after pull.
4. **No lookahead, ever.** If a strategy needs `.shift(-1)`, it's broken.
5. **Economic prior before stats.** A mechanism, then a backtest — not the reverse.
6. **Account for what you tried.** Trials logged; winners deflated for multiple testing.
7. **Costs are a measured input,** from paper fills — not a hopeful constant.
8. **Risk is config,** reviewable and revertible in git, never a code hotfix.
9. **The audit log is the source of truth.** If it isn't in `logs/*.jsonl`, it didn't happen.
