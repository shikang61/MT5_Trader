# Macro-Direction Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest whether a slow EURUSD-trend veto on the `donchian` and `london_orb` books cuts counter-macro losers — lowering drawdown + turnover on an existing edge.

**Architecture:** Two pure functions: `macro_signal(bars,...)` → `macro_dir` ∈ {−1,0,+1} (slow EURUSD EMA-slope with a neutral band), and `macro_gate(book_signal, macro_dir)` → vetoes book positions that disagree with `macro_dir` (only ever zeros a position). A research script applies the gate to each book's signal and compares gated vs ungated through the existing engine, plus a random-veto placebo.

**Tech Stack:** Python, polars, numpy, the repo's `uv` venv. Spec: `docs/superpowers/specs/2026-06-23-macro-gate-design.md`.

## Global Constraints

- Run everything via `POLARS_SKIP_CPU_CHECK=1 uv run ...` (the venv warns on this CPU).
- No lookahead: signals/series decide on bar `t` using data ≤ `t` (EMA + backward `.shift(+lag)` only — never `.shift(-k)`). The engine shifts execution +1 bar.
- Sign convention (verbatim): EURUSD **up** → USD down → gold-supportive → `macro_dir=+1` → **permit longs, veto shorts**. EURUSD **down** → `macro_dir=-1` → permit shorts, veto longs. Small |slope| (neutral band) → `macro_dir=0` → **no veto** (book signal unchanged).
- The gate **only ever zeros** a disagreeing position — never opens or flips one. Gated turnover ≤ ungated turnover, always.
- Data: `data/real` (XAUUSD + EURUSD, same source/clock, already ingested, May 11–Jun 10 2026, UTC).
- `BTResult.metrics` keys (from `backtest/metrics.py::summary`): `total_return`, `sharpe`, `sortino`, `max_drawdown`, `hit_rate`, `active_bars`, `n_bars`, `total_turnover`.
- `attach_eur(gold_bars, eur_bars) -> pl.DataFrame` (adds `eur_close`) already exists in `src/mt5_trader/strategies/usd_leadlag.py`.
- Books tested at their LIVE timeframes: `donchian` on **M5**, `london_orb` on **M1** (config/portfolio.yaml). `macro_signal` is computed on the SAME bars passed to the book, so `trend_span`/`slope_lag` are in bars-of-that-timeframe.
- Validation bar: gated must beat ungated on BOTH books at 4× cost with lower `max_drawdown` AND lower `total_turnover`, on a contiguous plateau, AND beat a random-veto placebo (p<0.05). Backtest-only — no live wiring in this plan.

---

### Task 1: `macro_signal` + `macro_gate` pure functions

**Files:**
- Create: `src/mt5_trader/strategies/macro_gate.py`
- Test: `tests/test_macro_gate.py`

**Interfaces:**
- Produces:
  - `macro_signal(bars: pl.DataFrame, trend_span: int = 480, slope_lag: int = 120, band: float = 0.0) -> np.ndarray` — reads `bars["eur_close"]`; returns `macro_dir` ∈ {−1.0, 0.0, +1.0}, length = len(bars). NaN warmup → 0.0.
  - `macro_gate(book_signal: np.ndarray, macro_dir: np.ndarray) -> np.ndarray` — element-wise veto; same length.

- [ ] **Step 1: Write the failing tests** — `tests/test_macro_gate.py`:

```python
from datetime import datetime, timedelta, timezone
import numpy as np
import polars as pl
from mt5_trader.strategies.macro_gate import macro_signal, macro_gate

T0 = datetime(2026, 5, 12, tzinfo=timezone.utc)
def _bars(eur):
    return pl.DataFrame({"ts": [T0 + timedelta(minutes=i) for i in range(len(eur))],
                         "eur_close": [float(x) for x in eur]})

def test_gate_vetoes_disagreeing_only():
    book = np.array([1.0, 1.0, -1.0, -1.0, 0.0])
    mdir = np.array([1.0, -1.0, 1.0, -1.0, 1.0])   # +1 permits long, -1 permits short
    out = macro_gate(book, mdir)
    # bar0 long & permit-long -> keep; bar1 long & permit-short -> veto 0;
    # bar2 short & permit-long -> veto 0; bar3 short & permit-short -> keep; bar4 flat -> 0
    assert out.tolist() == [1.0, 0.0, 0.0, -1.0, 0.0]

def test_gate_neutral_passes_through():
    book = np.array([1.0, -1.0, 1.0])
    mdir = np.array([0.0, 0.0, 0.0])     # neutral -> no veto
    assert macro_gate(book, mdir).tolist() == [1.0, -1.0, 1.0]

def test_gate_never_opens_or_flips():
    book = np.array([0.0, 1.0])
    mdir = np.array([1.0, -1.0])
    out = macro_gate(book, mdir)
    assert out[0] == 0.0          # never opens a position the book didn't ask for
    assert out[1] == 0.0          # disagree -> zero, never flips to -1

def test_macro_signal_rising_eur_is_plus_one():
    # steadily rising EURUSD -> positive slope -> macro_dir = +1 at the end
    eur = list(np.linspace(1.10, 1.12, 300))
    md = macro_signal(_bars(eur), trend_span=50, slope_lag=20, band=0.0)
    assert md[-1] == 1.0

def test_macro_signal_falling_eur_is_minus_one():
    eur = list(np.linspace(1.12, 1.10, 300))
    md = macro_signal(_bars(eur), trend_span=50, slope_lag=20, band=0.0)
    assert md[-1] == -1.0

def test_macro_signal_band_neutralizes_small_slope():
    # tiny drift, large band -> neutral (0)
    eur = list(np.linspace(1.1000, 1.1002, 300))
    md = macro_signal(_bars(eur), trend_span=50, slope_lag=20, band=0.01)  # 1% band
    assert md[-1] == 0.0

def test_macro_signal_no_lookahead_prefix_stable():
    rng = np.random.default_rng(0)
    eur = list(1.10 + np.cumsum(rng.normal(0, 1e-4, 400)))
    bars = _bars(eur)
    full = macro_signal(bars, trend_span=50, slope_lag=20, band=0.0)
    trunc = macro_signal(bars[:200], trend_span=50, slope_lag=20, band=0.0)
    assert np.allclose(full[:200], trunc, equal_nan=True)
```

- [ ] **Step 2: Run, verify fail**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest tests/test_macro_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: mt5_trader.strategies.macro_gate`.

- [ ] **Step 3: Implement** — `src/mt5_trader/strategies/macro_gate.py`:

```python
"""Macro-direction gate: veto trend-book trades that fight the slow USD trend.

A slow EURUSD EMA-slope gives a macro direction (EURUSD up -> USD down ->
gold-supportive). macro_gate zeros any book position that disagrees; it never
opens or flips a position, so gated turnover <= ungated. Backtest-only filter on
donchian/london_orb. See docs/superpowers/specs/2026-06-23-macro-gate-design.md.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def macro_signal(bars: pl.DataFrame, trend_span: int = 480,
                 slope_lag: int = 120, band: float = 0.0) -> np.ndarray:
    """Slow EURUSD trend direction in {-1,0,+1} (needs bars["eur_close"]).
    +1 = EURUSD rising (USD down, gold-supportive); -1 = falling; 0 = within the
    neutral band (|fractional slope| <= band). Causal: EMA + backward shift only."""
    ema = bars.select(pl.col("eur_close").ewm_mean(span=trend_span))["eur_close"].to_numpy()
    slope = np.full(len(ema), np.nan)
    if len(ema) > slope_lag:
        slope[slope_lag:] = ema[slope_lag:] / ema[:-slope_lag] - 1.0
    out = np.zeros(len(ema))
    out[slope > band] = 1.0
    out[slope < -band] = -1.0
    return out


def macro_gate(book_signal: np.ndarray, macro_dir: np.ndarray) -> np.ndarray:
    """Zero any book position that disagrees with macro_dir. macro_dir>0 permits
    longs (vetoes shorts), <0 permits shorts (vetoes longs), ==0 permits both.
    Only ever zeros a position — never opens or flips one."""
    book = np.asarray(book_signal, dtype=float)
    md = np.asarray(macro_dir, dtype=float)
    veto = ((md > 0) & (book < 0)) | ((md < 0) & (book > 0))
    return np.where(veto, 0.0, book)
```

- [ ] **Step 4: Run, verify pass**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest tests/test_macro_gate.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full suite (no regression)**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest -q`
Expected: all pass (162 existing + 7 new = 169).

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/strategies/macro_gate.py tests/test_macro_gate.py
git commit -m "feat(strategy): macro_signal + macro_gate pure functions"
```

---

### Task 2: Research harness (`research_macro_gate.py`) + gated-vs-ungated smoke

**Files:**
- Create: `scripts/research_macro_gate.py`

**Interfaces:**
- Consumes: `data/ingest.load_ticks`, `data/bars.time_bars`, `strategies.usd_leadlag.attach_eur`, `strategies.macro_gate.{macro_signal, macro_gate}`, `strategies.momentum_breakout.DonchianBreakout`, `strategies.session_vol.LondonBreakout`, `backtest/engine.{run, BTConfig}`.
- Produces: `build_bars(timeframe) -> pl.DataFrame` (gold bars at `timeframe` + `eur_close`, weekend-filtered, eur non-null); `BOOKS` (name→(strategy instance, timeframe)); `evaluate(book_name, trend_span, slope_lag, band, slip_bp) -> dict` returning ungated vs gated `total_return/sharpe/max_drawdown/total_turnover`.

- [ ] **Step 1: Write the script** — `scripts/research_macro_gate.py`:

```python
#!/usr/bin/env python
"""Macro-direction gate study: does a slow EURUSD-trend veto improve the trend books?

    uv run scripts/research_macro_gate.py sweep      # gated vs ungated grid, both books
    uv run scripts/research_macro_gate.py placebo     # random-veto placebo test
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.strategies.macro_gate import macro_gate, macro_signal
from mt5_trader.strategies.momentum_breakout import DonchianBreakout
from mt5_trader.strategies.session_vol import LondonBreakout
from mt5_trader.strategies.usd_leadlag import attach_eur

DATA = "data/real"
# book name -> (strategy instance, live timeframe)
BOOKS = {
    "donchian": (DonchianBreakout(), "5m"),
    "london_orb": (LondonBreakout(), "1m"),
}


def build_bars(timeframe: str) -> pl.DataFrame:
    gold = time_bars(load_ticks(DATA, "XAUUSD"), timeframe)
    eur = time_bars(load_ticks(DATA, "EURUSD"), timeframe)
    return attach_eur(gold, eur).drop_nulls("eur_close")


def _metrics(bars: pl.DataFrame, signal: np.ndarray, slip_bp: float) -> dict:
    m = run(bars, signal, BTConfig(slippage_bps=slip_bp)).metrics
    return {"ret": m["total_return"], "sharpe": m["sharpe"],
            "mdd": m["max_drawdown"], "turn": m.get("total_turnover", float("nan"))}


def evaluate(book_name: str, trend_span: int, slope_lag: int, band: float,
             slip_bp: float = 0.5) -> dict:
    strat, tf = BOOKS[book_name]
    bars = build_bars(tf)
    base = strat.signal(bars)
    mdir = macro_signal(bars, trend_span, slope_lag, band)
    gated = macro_gate(base, mdir)
    return {"ungated": _metrics(bars, base, slip_bp),
            "gated": _metrics(bars, gated, slip_bp),
            "n_bars": len(bars), "vetoed": int(((base != 0) & (gated == 0)).sum())}
```

- [ ] **Step 2: Smoke-run it** (after Task 1; data already present)

Run:
```bash
POLARS_SKIP_CPU_CHECK=1 uv run python -c "import scripts.research_macro_gate as r; \
import json; print(json.dumps(r.evaluate('donchian', 480, 120, 0.0), indent=2))"
```
Expected: a dict with `ungated` and `gated` sub-dicts (each `ret/sharpe/mdd/turn`), plus `n_bars` (~6–7k for M5 over ~22 trading days) and `vetoed` > 0 (the gate removed some trades). This proves the end-to-end path; the verdict comes from the sweep.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_macro_gate.py
git commit -m "feat(research): macro-gate harness (build_bars + evaluate, gated vs ungated)"
```

---

### Task 3: Sweep (trend_span × slope_lag × band), gated vs ungated, both books

**Files:**
- Modify: `scripts/research_macro_gate.py` (add `sweep` + `main`)

**Interfaces:**
- Consumes: Task 2's `BOOKS`, `evaluate`.
- Produces: per-book printed grid of ungated vs gated `ret/sharpe/mdd/turn` per (trend_span, slope_lag, band), flagging cells where gated beats ungated on return AND lower mdd AND lower turnover.

- [ ] **Step 1: Add the sweep** — append to `scripts/research_macro_gate.py`:

```python
SPAN_GRID = [240, 480, 960]      # bars of the book's timeframe
LAG_GRID = [60, 120, 240]
BAND_GRID = [0.0, 0.0005, 0.001, 0.002]   # fractional EURUSD slope dead-zone


def sweep(slip_bp: float = 0.5) -> None:
    for book in BOOKS:
        print(f"\n===== {book} (gated vs ungated, {slip_bp}bp slip) =====")
        base = evaluate(book, SPAN_GRID[0], LAG_GRID[0], BAND_GRID[0], slip_bp)["ungated"]
        print(f"UNGATED baseline: ret={base['ret']:+.4f} sharpe={base['sharpe']:.2f} "
              f"mdd={base['mdd']:.4f} turn={base['turn']:.1f}")
        print(f"{'span':>5}{'lag':>5}{'band':>8}{'g_ret':>9}{'g_sh':>7}{'g_mdd':>9}{'g_turn':>9}{'WIN?':>6}")
        wins = []
        for span in SPAN_GRID:
            for lag in LAG_GRID:
                for band in BAND_GRID:
                    e = evaluate(book, span, lag, band, slip_bp)
                    g, u = e["gated"], e["ungated"]
                    win = (g["ret"] > u["ret"] and g["mdd"] > u["mdd"] and g["turn"] < u["turn"])
                    # note: max_drawdown is negative; "lower DD" == closer to 0 == g['mdd'] > u['mdd']
                    print(f"{span:>5}{lag:>5}{band:>8.4f}{g['ret']:>9.4f}{g['sharpe']:>7.2f}"
                          f"{g['mdd']:>9.4f}{g['turn']:>9.1f}{'  YES' if win else '   no':>6}")
                    if win:
                        wins.append((span, lag, band))
        print(f"WIN cells (ret up, DD smaller, turnover down): {wins}")
        print("--- need a CONTIGUOUS plateau of WIN cells, not isolated hits ---")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "placebo"])
    args = ap.parse_args()
    if args.cmd == "sweep":
        sweep()
    elif args.cmd == "placebo":
        placebo()      # Task 4


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the sweep**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run scripts/research_macro_gate.py sweep`
Expected: two per-book tables (donchian, london_orb), each with an UNGATED baseline line and a 36-row grid, plus the WIN-cell list. **Verification (judgment):** does either book show a CONTIGUOUS block of WIN cells (gated beats ungated on return + smaller DD + lower turnover across neighbouring span/band)? Isolated wins → weak. Note the `max_drawdown` sign: it is negative, so "smaller drawdown" means `gated.mdd > ungated.mdd` (closer to 0) — already encoded in the `win` test.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_macro_gate.py
git commit -m "feat(research): macro-gate sweep (span x lag x band), gated vs ungated"
```

---

### Task 4: Random-veto placebo test

**Files:**
- Modify: `scripts/research_macro_gate.py` (add `placebo`)

**Interfaces:**
- Consumes: `BOOKS`, `build_bars`, `macro_signal`, `macro_gate`, `_metrics`; the best WIN params from Task 3.
- Produces: for the chosen (book, params), the macro-gated return vs the distribution of N random vetoes that each remove the SAME number of trades; prints p = P(random ≥ macro).

- [ ] **Step 1: Add the placebo** — append to `scripts/research_macro_gate.py`:

```python
# EDIT to a Task-3 WIN cell (book + params). If Task 3 found NO win, the study is
# negative — record it and skip the placebo (nothing to validate).
PLACEBO = dict(book="donchian", trend_span=480, slope_lag=120, band=0.001)


def _random_veto(base: np.ndarray, n_veto: int, rng) -> np.ndarray:
    """Zero n_veto randomly-chosen non-zero positions of `base` (placebo for the
    macro veto: same trade-count removed, but at random instead of by macro_dir)."""
    out = base.copy()
    nz = np.flatnonzero(base != 0)
    if n_veto > 0 and len(nz) >= n_veto:
        out[rng.choice(nz, size=n_veto, replace=False)] = 0.0
    return out


def placebo(n_perm: int = 200, seed: int = 0, slip_bp: float = 0.5) -> None:
    p = PLACEBO
    strat, tf = BOOKS[p["book"]]
    bars = build_bars(tf)
    base = strat.signal(bars)
    mdir = macro_signal(bars, p["trend_span"], p["slope_lag"], p["band"])
    gated = macro_gate(base, mdir)
    n_veto = int(((base != 0) & (gated == 0)).sum())
    macro_ret = _metrics(bars, gated, slip_bp)["ret"]
    print(f"{p['book']} {p}: macro veto removes {n_veto} positions; "
          f"macro gated ret={macro_ret:+.4f}")

    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    beat = 0
    for i in range(n_perm):
        rv = _random_veto(base, n_veto, rng)
        null[i] = _metrics(bars, rv, slip_bp)["ret"]
        beat += null[i] >= macro_ret
    pval = (beat + 1) / (n_perm + 1)
    print(f"random-veto null: mean={null.mean():+.4f} std={null.std():.4f}  "
          f"p(random>=macro)={pval:.4f}  -> {'PASS' if pval < 0.05 else 'FAIL'} (<0.05)")
```

- [ ] **Step 2: Run the placebo** (after editing `PLACEBO` to a Task-3 WIN cell)

Run: `POLARS_SKIP_CPU_CHECK=1 uv run scripts/research_macro_gate.py placebo`
Expected: prints the macro-gated return, the random-veto null distribution, and `p(random>=macro)`. **Verdict:** the gate is real (not just "trading less") only if `p < 0.05` — i.e. the macro veto beats removing the same number of trades at random. Combined with a Task-3 plateau on BOTH books, this clears the bar; otherwise document and shelve.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_macro_gate.py
git commit -m "feat(research): macro-gate random-veto placebo test"
```

---

## Self-review notes

- **Spec coverage:** macro signal + neutral band (Task 1 `macro_signal`), veto mechanism (Task 1 `macro_gate`), gated-vs-ungated on both books at the live timeframes (Tasks 2–3), DD+turnover improvement requirement (Task 3 `win` test), placebo (Task 4), plateau judgment (Task 3 Step 2). Live wiring is spec "out of scope" — correctly absent.
- **max_drawdown sign:** `summary` returns it negative; "lower DD" = closer to 0 = `gated.mdd > ungated.mdd`. Encoded consistently in Task 3's `win` and documented at its Step 2.
- **Timeframe faithfulness:** books run at their LIVE TFs (donchian M5, london_orb M1) so the result is relevant to the live portfolio; `macro_signal` is computed on the same bars, so span/lag are in bars-of-that-timeframe (noted in Global Constraints).
- **Deferred decision:** `PLACEBO` (Task 4) is edited to a Task-3 WIN cell — an explicit hand-off, not a placeholder; if Task 3 finds no win, the study is negative and Task 4 is skipped (documented in the code comment).
- **Types:** `macro_signal`/`macro_gate`/`build_bars`/`evaluate`/`_metrics` signatures consistent across tasks; metric keys (`total_return`,`sharpe`,`max_drawdown`,`total_turnover`) match `summary()`.
