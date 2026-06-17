# vwap_trend Regime Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in intraday trend-regime filter to `vwap_trend` so it stops trading during chop, then validate it clears the acceptance bar and wire it into the live portfolio config.

**Architecture:** `vwap_trend` is a session-VWAP stretch *continuation* strategy. Gate it with the existing Kaufman efficiency-ratio classifier (`features/regime.py`): force the position flat whenever the regime is RANGE (the inverse of `ratio_mr`, which trades only in RANGE). The regime is measured on a short intraday window (coarsen=4 ≈ 5h on M5), which the evidence sweep showed is the profitable region.

**Tech Stack:** Python, polars, numpy, pytest, `uv` (run everything via `uv run ...`).

## Global Constraints

- Shipped regime window: **coarsen = 4** (≈5h; plateau middle, most cost-robust). Verbatim from spec.
- `regime_filter` defaults to **False** — the change is inert until `config/portfolio.yaml` opts in (one-way code flow; reviewable deploy).
- Acceptance to ship the config flip: gated **max-drawdown smaller in magnitude AND Sharpe higher** than plain, on `data/real` (May 11 – Jun 10 2026). Provisional until re-confirmed on the competition dump.
- Causality is mandatory: the gated signal must pass the no-lookahead guard (`signal(bars)[:k] == signal(bars[:k])`).
- Scope: `vwap_trend` only. Do **not** touch `donchian` or portfolio weights.

---

### Task 1: Add the opt-in regime gate to vwap_trend (TDD)

**Files:**
- Modify: `src/mt5_trader/strategies/vwap_trend.py`
- Test: `tests/test_strategies.py`

**Interfaces:**
- Consumes: `mt5_trader.features.regime.regime_series(bars, coarsen=int) -> np.ndarray` (per-bar regime, values `TREND_UP=1 / RANGE=0 / TREND_DOWN=-1`, causal); `mt5_trader.features.regime.RANGE`.
- Produces: `VwapTrend(regime_filter: bool = False, regime_coarsen: int = 4)` — two new dataclass fields. `signal(bars)` return type and shape unchanged (`np.ndarray`, length `len(bars)`, values in `{-1,0,+1}`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategies.py`:

```python
def test_vwap_trend_regime_filter_off_is_default():
    """The gate is opt-in: default construction must equal regime_filter=False."""
    bars = make_bars(n=2000, seed=0)
    cls = REGISTRY["vwap_trend"]
    assert np.array_equal(cls().signal(bars), cls(regime_filter=False).signal(bars))


def test_vwap_trend_regime_gate_masks_range_bars():
    """regime_filter=True zeroes the position on every RANGE bar and leaves
    the position untouched on TREND bars."""
    from mt5_trader.features.regime import RANGE, regime_series
    bars = make_bars(n=2000, seed=0)
    cls = REGISTRY["vwap_trend"]
    ungated = cls(regime_filter=False).signal(bars)
    gated = cls(regime_filter=True, regime_coarsen=4).signal(bars)
    regime = regime_series(bars, coarsen=4)
    # non-vacuous: the strategy actually trades and both regimes occur
    assert (ungated != 0).any()
    assert (regime == RANGE).any() and (regime != RANGE).any()
    # the gate itself
    assert np.all(gated[regime == RANGE] == 0.0)
    assert np.array_equal(gated[regime != RANGE], ungated[regime != RANGE])


def test_vwap_trend_regime_gate_no_lookahead():
    """Gated signal stays causal: appending future bars must not change past
    signals (regime_series is anchored to the start, so this must hold)."""
    bars = make_bars(n=2000, seed=1)
    k = 1500
    cls = REGISTRY["vwap_trend"]
    full = cls(regime_filter=True, regime_coarsen=4).signal(bars)
    trunc = cls(regime_filter=True, regime_coarsen=4).signal(bars[:k])
    assert np.allclose(full[:k], trunc, equal_nan=True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_strategies.py -k "regime" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'regime_filter'`.

- [ ] **Step 3: Implement the gate**

In `src/mt5_trader/strategies/vwap_trend.py`:

Add the import below the existing `from .base import register`:
```python
from ..features.regime import RANGE, regime_series
```

Add two fields to the `VwapTrend` dataclass, immediately after `warmup_min`:
```python
    regime_filter: bool = False   # opt-in; gate the signal to TREND regimes only
    regime_coarsen: int = 4       # regime ER window ~ er_n(16) x coarsen x bar; 4 -> ~5h on M5
```

Replace the final `return pos` in `signal()` with:
```python
        if self.regime_filter:
            # continuation bleeds in chop: trade only when the intraday regime
            # is a trend (inverse of ratio_mr, which trades only in RANGE).
            regime = regime_series(bars, coarsen=self.regime_coarsen)
            pos = np.where(regime != RANGE, pos, 0.0)
        return pos
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_strategies.py -k "regime" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite (guards the default path + causality)**

Run: `uv run pytest -q`
Expected: all pass — `test_all_strategies_produce_valid_signals` and `test_no_lookahead` still green (default-off means the registry-instantiated strategy is unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/strategies/vwap_trend.py tests/test_strategies.py
git commit -m "feat(vwap_trend): opt-in intraday regime gate (default off)"
```

---

### Task 2: Reproduce the acceptance check + weekly walk-forward

**Files:**
- Modify: `scripts/eval_regime_gate.py`

**Interfaces:**
- Consumes: `VwapTrend(regime_filter=...)` from Task 1 (used indirectly: this script builds the gate by masking the plain signal, identical math to the strategy gate, so it does not depend on Task 1's fields — but Task 1 must land first so the shipped value matches).
- Produces: console output with an explicit PASS/FAIL per coarsen and a weekly breakdown. No code consumers.

This is a diagnostic script (repo convention: `eval_*.py` have no unit tests, cf. `eval_ratio_regime.py`). The deliverable is the recorded result + ship decision.

- [ ] **Step 1: Add a vwap_trend acceptance block**

In `scripts/eval_regime_gate.py`, after the existing `for strat in (...)` loop, add:

```python
    # --- vwap_trend acceptance: gate vs plain, coarsen 3/4, cost stress, weekly ---
    btc4x = BTConfig(slippage_bps=2.0, bars_per_year=bars_per_year(every))
    sig = REGISTRY["vwap_trend"]().signal(bars)
    plain = run(bars, sig, btc).metrics
    print("\n=== vwap_trend acceptance (DD smaller AND Sharpe higher = PASS) ===")
    show("plain", plain)
    for coarsen in (3, 4):
        gated = np.where(regime_series(bars, coarsen=coarsen) == RANGE, 0.0, sig)
        m = run(bars, gated, btc).metrics
        ok = (m["max_drawdown"] > plain["max_drawdown"]) and (m["sharpe"] > plain["sharpe"])
        show(f"gated c={coarsen} {'PASS' if ok else 'FAIL'}", m)
        show(f"gated c={coarsen} (4x cost)", run(bars, gated, btc4x).metrics)

    gated4 = np.where(regime_series(bars, coarsen=4) == RANGE, 0.0, sig)
    print("weekly net return (plain vs shipped gated c=4):")
    for label, pos in (("plain   ", sig), ("gated c=4", gated4)):
        wk = (run(bars, pos, btc).bars.with_columns(wk=pl.col("ts").dt.week())
              .group_by("wk").agg(ret=pl.col("pnl").sum()).sort("wk"))
        print(f"  {label}: " + "  ".join(
            f"w{r['wk']}:{r['ret']:+.4f}" for r in wk.iter_rows(named=True)))
```

(`max_drawdown` is negative, so `>` means "smaller in magnitude" = better.)

- [ ] **Step 2: Run it**

Run: `uv run scripts/eval_regime_gate.py`
Expected: the `vwap_trend acceptance` block prints `gated c=3 PASS` and `gated c=4 PASS` (DD ≈ −1 to −2% vs plain −6.96%, Sharpe positive vs −1.40), 4×-cost lines for c=3/c=4 positive, and the weekly line showing gated ≥ plain in most weeks.

- [ ] **Step 3: Record the verdict in the spec**

Append a short `## Validation result (2026-06-17)` section to `docs/superpowers/specs/2026-06-17-vwap-regime-gate-design.md` with the printed plain-vs-gated(c=4) numbers and the weekly line. State PASS/FAIL against the acceptance bar.

**Decision gate:** if c=4 prints FAIL, STOP — do not do Task 3. Report to the user; the gate did not clear and `vwap_trend` stays ungated.

- [ ] **Step 4: Commit**

```bash
git add scripts/eval_regime_gate.py docs/superpowers/specs/2026-06-17-vwap-regime-gate-design.md
git commit -m "test(vwap_trend): regime-gate acceptance check + recorded result"
```

---

### Task 3: Wire the gate into the live portfolio (only if Task 2 PASSED)

**Files:**
- Modify: `config/portfolio.yaml`

**Interfaces:**
- Consumes: `run_portfolio.py:28` constructs `REGISTRY[b["strategy"]](**b.get("params", {}))`, so the YAML `params` keys must match the `VwapTrend` field names exactly (`regime_filter`, `regime_coarsen`).
- Produces: live `vwap_trend` book runs with the gate on.

- [ ] **Step 1: Add the params to the vwap_trend book**

In `config/portfolio.yaml`, change the `vwap_trend` book from:
```yaml
  - strategy: vwap_trend
    symbol: XAUUSD
    timeframe: M5
    weight: 0.3
```
to:
```yaml
  - strategy: vwap_trend
    symbol: XAUUSD
    timeframe: M5
    weight: 0.3
    params: {regime_filter: true, regime_coarsen: 4}
    # Intraday TREND gate (eff-ratio): plain was -2.7%/Sh-1.4/DD-6.96% on the real
    # feed (May11-Jun10); gated c=4 +1.1%/Sh2.2/DD-2.18%, +ve under 4x cost. coarsen
    # plateau 1-4, cliff at >=5. PROVISIONAL: re-confirm on the competition dump.
```

- [ ] **Step 2: Sanity-check the config constructs the gated strategy**

Run:
```bash
uv run python -c "
import yaml
from mt5_trader.strategies import REGISTRY
books = yaml.safe_load(open('config/portfolio.yaml'))['books']
b = next(x for x in books if x['strategy']=='vwap_trend')
s = REGISTRY[b['strategy']](**b.get('params', {}))
print('regime_filter=', s.regime_filter, 'coarsen=', s.regime_coarsen)
assert s.regime_filter is True and s.regime_coarsen == 4
print('OK')
"
```
Expected: `regime_filter= True coarsen= 4` then `OK`.

- [ ] **Step 3: Run the full suite once more**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add config/portfolio.yaml
git commit -m "feat(portfolio): enable vwap_trend regime gate (coarsen=4, provisional)"
```

---

## Self-Review

**Spec coverage:**
- Code change (import + 2 fields + gate) → Task 1. ✓
- Tests (regression default-off, masking, causality) → Task 1 Step 1. ✓
- Validation harness (plain vs gated c=3/c=4, weekly walk-forward, 4× cost, acceptance verdict) → Task 2. ✓
- Config opt-in with cited numbers + rollback (delete params line) → Task 3. ✓
- Out-of-scope (donchian, sizing) → not touched; Global Constraints + Task 3 Step 1 keep weight 0.3. ✓
- Competition-dump OOS re-confirm → noted as provisional in Task 3 comment + spec; not actionable until dump lands (~Jun 21). ✓

**Placeholder scan:** no TBD/TODO; all code shown verbatim. ✓

**Type consistency:** `regime_filter` (bool), `regime_coarsen` (int) used identically in vwap_trend.py, tests, eval script, and portfolio.yaml. `regime_series(bars, coarsen=...)` and `RANGE` match `features/regime.py`. `max_drawdown`/`sharpe`/`total_return` match the metrics dict used in `eval_ratio_regime.py`. ✓
