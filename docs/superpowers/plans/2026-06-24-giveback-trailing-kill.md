# give_back Daily Trailing Kill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **OUTCOME (2026-06-24): left OFF.** Task 1 ran; the sweep table is flat — every
> `g` identical to baseline (worst intraday net retrace < 1%, so no `g` ever fires).
> Task 2 (config) not applied; `posture.give_back` stays 0.0. Research script kept;
> revisit on a choppy/shock month. See spec OUTCOME.

**Goal:** Find (backtest) and, if it clears the gate, enable a daily trailing-kill fraction `g` that flattens the net book for the rest of a UTC day once net equity retraces `g` from the day's peak.

**Architecture:** The live mechanism already exists and is unit-tested (`RiskManager.give_back` / `.kill` / `.roll_day`). This is **validate-then-enable**: a net-level backtest sweeps `g`, then a one-line `config/risk.yaml` change. No new live code.

**Tech Stack:** Python 3.11, polars, numpy, `uv run`.

## Global Constraints

- The daily-kill overlay in the backtest must mirror `RiskManager.kill`/`roll_day` exactly: per UTC day anchor `day_start` + `day_peak` at the first bar; update `day_peak = max(...)`; trip (flatten rest of UTC day, sticky) when `equity/day_peak - 1 <= -give_back` OR `equity/day_start - 1 <= -loss_limit` (loss_limit = 0.04); reset next UTC day.
- Net-level only (the blended book is what trades — the lesson of `profit-ratchet-dead`).
- Real broker feed (`build_bars`). No new live code; `RiskManager` is unchanged.
- Phase 2 (config) only after the Task 1 gate + user sign-off.

---

### Task 1: Net-level give_back sweep `research_giveback_net.py` — GATE

**Files:**
- Create: `scripts/research_giveback_net.py`

Research script (run-and-report; matches the other `scripts/research_*.py`, no unit test). The net-frac construction is copied verbatim from `scripts/research_scaleout_net.py` (the repo's research scripts are self-contained). **STOP at the gate.**

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python
"""RESEARCH: does a daily trailing kill (give_back) help the NET book?

Sweeps give_back g: once net equity retraces g from the day's PEAK, flatten for the
rest of the UTC day (mirrors RiskManager.kill/roll_day). loss_limit=0.04 (kill from
day-START) is always active, matching live. Relative comparison, one window.

    uv run scripts/research_giveback_net.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import yaml

from mt5_trader.backtest.metrics import summary
from mt5_trader.features.technicals import atr, realized_vol
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

SYMBOL = "XAUUSD"
TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
SLIP = 0.5e-4
TARGET_VOL, MAX_LEV, VOL_WIN = 0.30, 2.0, 240
SL_ATR_MULT, ATR_WIN, LOSS_LIMIT = 8.0, 240, 0.04
_cache: dict[str, pl.DataFrame] = {}


def gb(every: str) -> pl.DataFrame:
    if every not in _cache:
        _cache[every] = build_bars(SYMBOL, every).sort("ts")
    return _cache[every]


def book_frac_1m(book: dict, master: pl.DataFrame) -> np.ndarray:
    name, tf = book["strategy"], book.get("timeframe", "M1")
    every, w = TF_EVERY[tf], float(book.get("weight", 1.0))
    params = book.get("params", {}) or {}
    bars = (spread_bars(gb(every), build_bars("XAGUSD", every).sort("ts"), 1.0)
            if name == "ratio_mr" else gb(every))
    sig = np.asarray(REGISTRY[name](**params).signal(bars), dtype=float)
    vol = bars.select(realized_vol(VOL_WIN, bars_per_year(every))).to_series().to_numpy()
    lev = np.minimum(TARGET_VOL / np.maximum(vol, 1e-4), MAX_LEV)
    frac = np.nan_to_num(sig * lev) * w
    sdf = pl.DataFrame({"ts": bars["ts"], "s": frac}).sort("ts")
    joined = master.select("ts").join_asof(sdf, on="ts", strategy="backward")
    return np.nan_to_num(joined["s"].to_numpy())


def sim(net_frac, close, high, low, atr_arr, sp_frac, dates, give_back) -> dict:
    """Net bar-by-bar sim: chandelier 8xATR disaster stop + daily kill overlay
    (loss_limit from day-start, give_back from day-peak). Returns metrics.summary."""
    n = len(close)
    pnl = np.zeros(n)
    turnover = np.zeros(n)
    atr_entry = entry = extreme = side = 0.0
    re_anchor = True
    prev_eff = 0.0
    equity = 1.0
    cur_day = None
    day_start = day_peak = 1.0
    killed = False

    for t in range(1, n):
        if dates[t] != cur_day:                 # new UTC day: reset anchors
            cur_day, day_start, day_peak, killed = dates[t], equity, equity, False

        raw = 0.0 if killed else net_frac[t - 1]   # one-bar delay; flat if killed today
        prev_close = close[t - 1]

        if raw == 0.0:
            side, re_anchor = 0.0, True
            eff = 0.0
            turnover[t] = abs(eff - prev_eff)
            pnl[t] = -turnover[t] * (sp_frac[t] / 2 + SLIP)
            prev_eff = eff
        else:
            long = raw > 0
            if re_anchor or long != (side > 0):
                atr_entry, entry, extreme = atr_arr[t - 1], prev_close, prev_close
                re_anchor = False
            side = raw
            extreme = max(extreme, high[t]) if long else min(extreme, low[t])
            eff = raw
            direction = 1.0 if long else -1.0
            exit_px = None
            if SL_ATR_MULT > 0 and atr_entry > 0:
                sl = extreme - direction * SL_ATR_MULT * atr_entry
                if (long and low[t] <= sl) or (not long and high[t] >= sl):
                    exit_px = sl
            if exit_px is not None:
                bar_ret = exit_px / prev_close - 1.0
                turnover[t] = abs(eff - prev_eff) + 2.0 * abs(eff)
                pnl[t] = eff * bar_ret - turnover[t] * (sp_frac[t] / 2 + SLIP)
                re_anchor, prev_eff = True, 0.0
            else:
                bar_ret = close[t] / prev_close - 1.0
                turnover[t] = abs(eff - prev_eff)
                pnl[t] = eff * bar_ret - turnover[t] * (sp_frac[t] / 2 + SLIP)
                prev_eff = eff

        equity *= 1.0 + pnl[t]
        day_peak = max(day_peak, equity)
        if not killed and (equity / day_start - 1.0 <= -LOSS_LIMIT
                           or (give_back > 0 and equity / day_peak - 1.0 <= -give_back)):
            killed = True

    return summary(pnl, np.cumprod(1.0 + pnl), bars_per_year("1m"), turnover)


def main():
    cfg = yaml.safe_load(open("config/portfolio.yaml"))
    master = gb("1m")
    close = master["close"].to_numpy().astype(float)
    high = master["high"].to_numpy().astype(float)
    low = master["low"].to_numpy().astype(float)
    sp_frac = (master["spread_mean"].fill_null(0).to_numpy().astype(float)
               / np.where(close == 0, 1.0, close))
    atr_arr = master.select(atr(ATR_WIN)).to_series().to_numpy().astype(float)
    dates = [d.date() for d in master["ts"].to_list()]
    net_frac = sum(book_frac_1m(b, master) for b in cfg["books"])

    print(f"1m grid: {master.height} bars  {master['ts'][0]} -> {master['ts'][-1]}")
    print(f"{'give_back':>10}{'return%':>10}{'sharpe':>8}{'maxDD%':>10}{'turnover':>10}")
    print("-" * 48)
    for g in [0.0, 0.01, 0.015, 0.02, 0.03, 0.04]:
        m = sim(net_frac, close, high, low, atr_arr, sp_frac, dates, g)
        tag = "0 (base)" if g == 0 else f"{g:.3f}"
        print(f"{tag:>10}{m['total_return']*100:>+10.3f}{m['sharpe']:>8.2f}"
              f"{m['max_drawdown']*100:>+10.3f}{m['total_turnover']:>10.0f}")
    print("\nRelative read only: ~1 month, one regime. give_back=0 -> loss_limit (4%) only.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run scripts/research_giveback_net.py`
Expected: a `give_back` column (0 = baseline) with return / Sharpe / maxDD / turnover. (Bars are disk-cached; first run may take minutes, then fast.)

- [ ] **Step 3: Commit**

```bash
git add scripts/research_giveback_net.py
git commit -m "feat(research): net-level give_back daily-trailing-kill sweep"
```

- [ ] **Step 4: GATE — report + wait**

Does any `g` cut net maxDD / the left tail materially while holding return + Sharpe, on a plateau (neighbouring `g` agree)? If the window has no big intraday giveback day, expect `g` to do nothing or only cost return → drop. Recommend a `g` or "none — keep give_back off." **Wait for sign-off before Task 2.**

---

### Task 2: Enable give_back in config — POST-GATE

**Files:**
- Modify: `config/risk.yaml`

- [ ] **Step 1: Set the value**

If a `g` cleared the gate, set it in `config/risk.yaml` under `posture` (the field already exists, default 0.0):

```yaml
  give_back:    <g>     # daily trailing kill: flatten if equity falls this frac from the day's PEAK
```

Add a one-line comment citing the sweep result (return/maxDD vs baseline). If nothing cleared, leave `give_back: 0.0` and stop.

- [ ] **Step 2: Validate config loads + suite green**

Run: `uv run python -c "from mt5_trader.risk.manager import RiskManager; rm=RiskManager.from_yaml('config/risk.yaml'); print('give_back', rm.posture.give_back)"`
Then: `uv run pytest -q`
Expected: prints the new give_back; suite passes (the kill path is already covered by `tests/test_risk.py`).

- [ ] **Step 3: Commit**

```bash
git add config/risk.yaml
git commit -m "config(risk): enable give_back daily trailing kill (cleared the sweep)"
```

Deploy (after merge): VPS `git pull` + restart `MT5Portfolio` via `Stop+Start-ScheduledTask`. (No code change — `RiskManager` already enforces give_back; only the config value changed.)

---

## Self-Review

**Spec coverage:**
- Net-level give_back backtest mirroring `RiskManager.kill` (day-start loss_limit + day-peak give_back, sticky, daily reset) → Task 1 `sim`. ✓
- Sweep + gate (cut maxDD/tail without gutting return, on a plateau) → Task 1 Step 4. ✓
- Enable via config only, no live code → Task 2. ✓
- Mechanism already tested → noted; suite (`test_risk.py`) re-run in Task 2. ✓

**Placeholder scan:** none — full script + exact commands. `<g>` in Task 2 is the gate output, intentionally filled at deploy time.

**Type consistency:** `sim(net_frac, close, high, low, atr_arr, sp_frac, dates, give_back)` used as defined; `summary(pnl, equity, periods_per_year, turnover)` matches `backtest/metrics.py`; daily-kill constants (`LOSS_LIMIT=0.04`) match `config/risk.yaml`.
