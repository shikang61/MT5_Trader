# Onboarding — start here

Welcome. This is a quant research + live-trading system for the **Model to
Market** competition (XAUUSD-focused, MT5 execution). This page gets you from
zero to productive; the deeper docs are linked at the end. Read this top to
bottom once, run the setup, then follow the "what to read next" path.

---

## 1. The 60-second mental model

Two boxes, one-way code flow:

```
macOS (research)                         Windows VPS (live)
  CSV dump → parquet ticks                 MT5 terminal
    → features (OFI, technicals)             ▲ IPC
      → bars (time/tick)                     │
        → strategy.signal(bars)         MT5Gateway → runner loop
          → backtest engine + metrics     every bar: bars → signal
                                            → risk-size → reconcile position
        ── git (code + frozen config) ──►   → JSONL audit log
```

The single idea that makes it work: **a strategy is one function,
`signal(bars) -> target positions`, and the *same code* runs in the backtester
and live.** No reimplementation = no research-vs-live drift. The VPS only
`git pull`s; nothing is edited there.

## 2. The contract you must know (`strategies/base.py`)

A strategy is any object with:

```python
name: str                                  # unique registry key
def signal(self, bars: pl.DataFrame) -> np.ndarray   # one value per bar, in [-1, 1]
```

- `signal[t]` is the **target position** decided on bar `t`'s close: `+1` full
  long, `-1` full short, `0` flat, fractions = partial size.
- **Causality is sacred.** `signal[t]` may use data only up to bar `t`. Rolling
  windows and `.shift(1)` are fine; `.shift(-1)` or any future data is lookahead
  and silently inflates every backtest. The engine/live runner fill the signal
  **one bar later**, so peeking forward is also wrong about timing.
- This is enforced: `tests/test_strategies.py::test_no_lookahead` asserts
  `signal(bars)[:k] == signal(bars[:k])` for every registered strategy. If you
  add a strategy that peeks, that test goes red.
- `bars` columns: `ts` (UTC), `open/high/low/close`, `spread_mean`, `n_ticks`,
  and `ofi` when built with the order-flow aggregation.

Strategies self-register via `@register` and are resolved by short `name`
through `REGISTRY` (see `strategies/README.md` for the **file ↔ name** map —
note `donchian` lives in `momentum_breakout.py`, `london_orb` in
`session_vol.py`, etc.).

## 3. Get running (macOS research)

```bash
uv sync --extra dev
uv run pytest                       # full suite, should be all green
uv run scripts/run_backtest.py --strategy donchian --symbol XAUUSD
```

The `MetaTrader5` package is Windows-only; on macOS the package still **imports**
(so research code runs) but constructing `MT5Gateway()` raises. You never need a
broker connection to research, backtest, or run the tests.

When the historical dump arrives: edit `config/schema_map.yaml` to match the CSV
columns, drop CSVs into `data/raw/`, then `uv run scripts/ingest_dump.py data/raw`.

## 4. Repo map

```
src/mt5_trader/
  data/        ingest CSV → canonical parquet ticks; bars.py (time/tick bars)
  features/    technicals (ema/atr/rsi/zscore/vol), microstructure (OFI),
               regime.py (Kaufman efficiency-ratio trend/range), calendar, vol_guard
  backtest/    engine.py (vectorized, no-lookahead by construction) + metrics + attribution
  strategies/  the books — registry-based; base.py defines the contract
  pipeline/    dev-pipeline logic: data, diagnostics, validate (gates), stash, trials
  risk/        manager.py — round-aware vol targeting + kill switch
  live/        mt5_gateway.py (broker wrapper), runner.py (the live loop),
               passive_limits.py (parked experiment), single_instance.py (dup-launch guard)
config/        portfolio.yaml (deployed books + weights), risk.yaml (round budgets),
               instruments.yaml, schema_map.yaml (dump column map)
research/      stash.yaml (sub-par candidates to improve); trials/verdicts (gitignored)
scripts/       strategy.py (pipeline CLI), run_backtest / run_live / run_portfolio,
               eval_* studies, fetch_* data, watchdog, *.bat VPS launchers
tests/         pytest suite (run before every push)
docs/          the deep docs (see §8)
```

## 5. Backtest vs live — the two execution paths

- **Backtest** (`backtest/engine.py`): `run(bars, signal, cfg)`. Positions are the
  one-bar-delayed signal; each unit of turnover pays half the bar spread +
  `slippage_bps`. Optional intrabar ATR stops mirror the live disaster stop.
- **Live** (`live/runner.py`): `PortfolioRunner` runs every book in
  `config/portfolio.yaml` in one loop. Each cycle: fetch bars → per-book signal →
  risk-size by book weight → **net targets per symbol across books** → reconcile
  the MT5 position. `LiveRunner` (single book) and `PairRunner` (two-leg spread)
  are thinner variants. All four runners share `resilient_loop` (survives errors,
  reconnects after 3 failures) and `_kill_check` (the kill-switch preamble).

Key live invariants:
- **Magic numbers segregate orders on one account.** Portfolio bot = `1001`;
  `reconcile`/`flatten` only touch matching tickets, so multiple bots never net
  against each other. (The parked passive books used `2002`/`2003`.)
- **`equity` is account-wide** = balance + floating P&L of all open positions.
  It drives both position sizing and the kill switch.
- Everything is appended to a JSONL audit log — that log is the source of truth.

## 6. Risk model (`risk/manager.py` + `config/risk.yaml`)

- **Vol targeting:** size = `min(target_vol / realized_vol, max_leverage)`,
  scaled by the strategy signal and the book weight.
- **Kill switch (per round):** flatten everything + halt for the round if equity
  drops `loss_limit` from round-start (floor) or `give_back` from the round's
  peak (trailing). Resets each round.
- **Overlays:** economic-calendar blackout (force flat around high-impact USD
  events) and a fast vol-spike circuit breaker. Both gate-able in `risk.yaml`.

## 7. Adding a strategy (the happy path)

The pipeline CLI `scripts/strategy.py` drives this: `list` (the board),
`diagnose` (signal stats), `validate` (the gates), `stash` (park sub-par ideas
to improve), `promote` (gated deploy). Steps:

1. `uv run scripts/strategy.py new my_strat` — scaffolds from the template.
2. Rename the class, set a unique `name`, write `signal()`, uncomment `@register`.
3. Add it to `strategies/__init__.py` imports (populates the registry).
4. `uv run pytest` (the no-lookahead + valid-signal guards now cover it).
5. `uv run scripts/strategy.py diagnose my_strat` then `validate my_strat`.
6. On PASS → `uv run scripts/strategy.py promote my_strat --weight <w>` (adds it
   to `portfolio.yaml`). On FAIL → `strategy.py stash add my_strat --improve
   "..."` and iterate. See **WORKFLOW.md** stages 2–3 + 9.

## 8. What to read next (in order)

1. **`docs/ARCHITECTURE.md`** — the two-box design in more depth.
2. **`docs/WORKFLOW.md`** — the alpha lifecycle (data → hypothesis → backtest →
   portfolio → risk → paper → deploy → monitor), and the QR/QD split.
3. **`docs/STRATEGY_PLAYBOOK.md`** — the deployed book lineup, per round, with the
   research evidence behind each.
4. **`docs/COMPETITION_PLAN.md`** — dates, round cutoffs, the day-by-day checklist.
5. **`docs/LESSONS.md`** — hard-won findings (costs decide everything; timeframe
   is a first-class parameter; hot cells aren't edges). Read before researching.
6. **`src/mt5_trader/*/README.md`** — one per package, explaining its files.

## 9. Current state & gotchas (as of this writing)

- **Deployed books:** `donchian`, `vwap_trend`, `london_orb`, `sweep_fade`,
  `ratio_mr` (see `config/portfolio.yaml` for weights). Benched: `regime_switch`,
  `ofi`, `fast_mr`.
- **Passive limit-order experiment is PARKED.** It lost money in live paper
  trading (adverse selection on fills), so `start_all.bat` no longer launches it.
  The code (`live/passive_limits.py`, `scripts/run_passive_paper.py`,
  `scripts/start_passive.bat`) is kept for a possible future revisit.
- **Live ops:** the VPS runs one command — `scripts/start_all.bat` (portfolio bot
  only now). Credentials come from env vars `MT5_LOGIN` / `MT5_PASSWORD` /
  `MT5_SERVER` (never committed). `single_instance.py` refuses a duplicate launch.
  Stopping a live bot is a VPS action — removing code here does **not** stop a
  running process.
