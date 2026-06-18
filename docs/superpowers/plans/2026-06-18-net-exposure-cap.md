# Net-Exposure Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound the portfolio's netted directional position per symbol by clipping the summed book target to a configurable leverage-multiple-of-equity ceiling, so aligned books cannot stack into an oversized bet.

**Architecture:** A pure `cap_lots` helper (module-level in `live/runner.py`, beside `chandelier_sl`/`protective_levels`) converts net lots to a leverage fraction `lots*contract*price/equity`, clips it to `±cap`, and converts back — preserving sign, no-op when `cap<=0`. `PortfolioRunner.step` calls it per symbol after the `vol_halt` zeroing and before reconcile, logging when the cap binds. The cap value flows `portfolio.yaml` → `run_portfolio.py` → `RunnerCfg.exposure_cap`.

**Tech Stack:** Python, polars, pytest. `cap_lots` is pure (no MT5); `PortfolioRunner` tested with a fake gateway (no MT5, no VPS). Run tests with `uv run pytest`.

## Global Constraints

- Cap unit: leverage multiple of equity — `frac = net_lots * contract_size * price / equity`. Deploy value **0.5**. `cap <= 0` disables.
- Per-symbol cap (acts on the netted lots across books for one symbol), applied **after** `vol_halt` zeroing, **before** sl/tp + reconcile.
- `cap_lots` preserves sign (long stays long, short stays short) and is a no-op when `cap<=0`, `equity<=0`, or `price<=0`.
- `RunnerCfg.exposure_cap` defaults to `0.0` (off), matching the `sl_atr_mult` disabled-by-zero convention. Only `PortfolioRunner` reads it.
- Do NOT touch: kill switch, `vol_guard`, blackout, per-book sizing, `LiveRunner`/`PairRunner` (single-book), passive book, strategies, reopen_fade (shelved).

---

### Task 1: Net-exposure cap (`cap_lots` helper + PortfolioRunner wiring + config)

**Files:**
- Modify: `src/mt5_trader/live/runner.py` (add `cap_lots` near line 72; add `exposure_cap` to `RunnerCfg` ~line 24-34; wire into `PortfolioRunner.step` ~line 340-355)
- Modify: `scripts/run_portfolio.py:67-71` (pass `exposure_cap` into `RunnerCfg`)
- Modify: `config/portfolio.yaml` (add top-level `exposure_cap: 0.5`)
- Test: `tests/test_exposure_cap.py` (new)

**Interfaces:**
- Produces:
  - `cap_lots(lots: float, contract_size: float, price: float, equity: float, cap: float) -> tuple[float, bool]` — returns `(possibly_clipped_lots, bound)`.
  - `RunnerCfg.exposure_cap: float = 0.0` — new dataclass field.
  - `PortfolioRunner.step` per-symbol `fills[sym]` dict gains a `"cap": bool` key.
- Consumes: `RunnerCfg` (existing), `PortfolioRunner` (existing), `_BarCache.spec(sym)["contract_size"]` (existing), the `equity` already returned by `_kill_check` in `step`.

- [ ] **Step 1: Write the failing unit tests for `cap_lots`**

Create `tests/test_exposure_cap.py`:

```python
from mt5_trader.live.runner import cap_lots

# At equity 100_000, contract 100, price 4000: 1.0 frac == 0.25 lots
# (0.25 * 100 * 4000 / 100_000 = 1.0).

def test_above_cap_clipped_long():
    lots, bound = cap_lots(0.25, 100.0, 4000.0, 100_000.0, 0.5)
    assert bound is True
    assert abs(lots - 0.125) < 1e-9          # 0.5 frac -> 0.125 lots
    assert lots > 0                          # sign preserved (long)

def test_above_cap_clipped_short():
    lots, bound = cap_lots(-0.25, 100.0, 4000.0, 100_000.0, 0.5)
    assert bound is True
    assert abs(lots + 0.125) < 1e-9          # clipped to -0.125 lots
    assert lots < 0                          # sign preserved (short)

def test_clipped_frac_equals_cap():
    lots, bound = cap_lots(0.40, 100.0, 4000.0, 100_000.0, 0.5)
    frac = lots * 100.0 * 4000.0 / 100_000.0
    assert bound is True
    assert abs(abs(frac) - 0.5) < 1e-9       # exposure sits exactly at the cap

def test_below_cap_unchanged():
    lots, bound = cap_lots(0.10, 100.0, 4000.0, 100_000.0, 0.5)
    assert bound is False
    assert lots == 0.10                      # 0.4 frac < 0.5 -> untouched

def test_cap_zero_disables():
    lots, bound = cap_lots(5.0, 100.0, 4000.0, 100_000.0, 0.0)
    assert bound is False
    assert lots == 5.0

def test_nonpositive_equity_or_price_safe():
    assert cap_lots(5.0, 100.0, 4000.0, 0.0, 0.5) == (5.0, False)
    assert cap_lots(5.0, 100.0, 0.0, 100_000.0, 0.5) == (5.0, False)
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `uv run pytest tests/test_exposure_cap.py -q`
Expected: FAIL at import — `ImportError: cannot import name 'cap_lots' from 'mt5_trader.live.runner'`.

- [ ] **Step 3: Implement `cap_lots` in `src/mt5_trader/live/runner.py`**

Add this function at module level, immediately after `pair_leg_lots` (around line 79, before `resilient_loop`):

```python
def cap_lots(lots: float, contract_size: float, price: float,
             equity: float, cap: float) -> tuple[float, bool]:
    """Clip net lots so |exposure| <= cap, where exposure is the leverage
    multiple of equity (lots * contract_size * price / equity). Preserves sign.
    cap <= 0 (or non-positive equity/price) disables. Returns (lots, bound)."""
    if cap <= 0 or equity <= 0 or price <= 0:
        return lots, False
    frac = lots * contract_size * price / equity
    if abs(frac) <= cap:
        return lots, False
    capped = (cap if frac > 0 else -cap) * equity / (contract_size * price)
    return capped, True
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `uv run pytest tests/test_exposure_cap.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Add `exposure_cap` to `RunnerCfg`**

In `src/mt5_trader/live/runner.py`, in the `RunnerCfg` dataclass (around line 24-34), add the field after `magic`:

```python
    magic: int | None = None   # tag orders so strategies can share one account
    exposure_cap: float = 0.0  # cap |net directional exposure| as a leverage
                               # multiple of equity (lots*contract*price/equity);
                               # 0 = off. PortfolioRunner only.
    log_path: Path = Path("logs/live.jsonl")
```

- [ ] **Step 6: Wire `cap_lots` into `PortfolioRunner.step`**

In `src/mt5_trader/live/runner.py`, inside `PortfolioRunner.step`'s per-symbol loop, the current block reads:

```python
            price = float(bars["close"][-1])
            high, low = float(bars["high"][-1]), float(bars["low"][-1])
            atr_val = float(bars.select(atr(self.cfg.atr_window)).to_series()[-1] or 0.0)
            sl, self._trail[sym] = chandelier_sl(lots, price, high, low, atr_val,
                                                 self.cfg.sl_atr_mult,
                                                 self._trail.get(sym))
            _, atr_entry, entry_price, _ = self._trail[sym]
            _, tp = protective_levels(lots, entry_price, atr_entry,
                                      0.0, self.cfg.tp_atr_mult)
            fills[sym] = {"target_lots": lots, "sl": sl, "tp": tp,
                          "price": price, "vol_halt": halt,
                          "contract_size": self._cache.spec(sym)["contract_size"],
                          "traded_lots": self.gw.reconcile(sym, lots, sl=sl, tp=tp,
                                                           magic=self.cfg.magic)}
```

Change it to bind the cap right after `price`, before the stop math, and record the flag (note `contract` is fetched once and reused in the `fills` dict):

```python
            price = float(bars["close"][-1])
            contract = self._cache.spec(sym)["contract_size"]
            lots, capped = cap_lots(lots, contract, price, equity, self.cfg.exposure_cap)
            high, low = float(bars["high"][-1]), float(bars["low"][-1])
            atr_val = float(bars.select(atr(self.cfg.atr_window)).to_series()[-1] or 0.0)
            sl, self._trail[sym] = chandelier_sl(lots, price, high, low, atr_val,
                                                 self.cfg.sl_atr_mult,
                                                 self._trail.get(sym))
            _, atr_entry, entry_price, _ = self._trail[sym]
            _, tp = protective_levels(lots, entry_price, atr_entry,
                                      0.0, self.cfg.tp_atr_mult)
            fills[sym] = {"target_lots": lots, "sl": sl, "tp": tp,
                          "price": price, "vol_halt": halt, "cap": capped,
                          "contract_size": contract,
                          "traded_lots": self.gw.reconcile(sym, lots, sl=sl, tp=tp,
                                                           magic=self.cfg.magic)}
```

(`lots` is already 0.0 here if `vol_halt` tripped above — `cap_lots` leaves 0 untouched, so halt still wins.)

- [ ] **Step 7: Write the failing PortfolioRunner integration test**

Append to `tests/test_exposure_cap.py`:

```python
import json
from datetime import datetime, timezone

from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg
from mt5_trader.risk.manager import RiskManager, Posture

NOW = datetime.now(timezone.utc)


class _Strat:
    """Constant full-long signal so the netted book exposure is large."""
    def signal(self, bars):
        import numpy as np
        return np.ones(len(bars))


class CapFakeGW:
    """Minimal gateway: one symbol, fixed bars/spec/price, records reconcile lots."""
    def __init__(self, equity, price=4000.0, contract=100.0):
        self._eq, self._price, self._contract = equity, price, contract
        self.reconciled = {}

    def equity(self):
        return self._eq

    def symbol_spec(self, symbol):
        return {"contract_size": self._contract, "lot_step": 0.01,
                "lot_min": 0.01, "lot_max": 100.0}

    def bars(self, symbol, timeframe="M1", n=3000):
        # 300 constant-price bars: > the 240-bar vol window so realized_vol is
        # defined (== 0 on a flat price), which makes risk.size's leverage hit the
        # max_leverage=2 cap deterministically -> a known, over-cap netted exposure.
        import polars as pl
        from datetime import timedelta
        base = datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)
        ts = [base + timedelta(minutes=i) for i in range(300)]
        return pl.DataFrame({
            "ts": ts,
            "open": [self._price] * 300, "high": [self._price] * 300,
            "low": [self._price] * 300, "close": [self._price] * 300,
            "spread_mean": [0.1] * 300, "tick_volume": [1.0] * 300,
        })

    def reconcile(self, symbol, lots, sl=0.0, tp=0.0, magic=None):
        self.reconciled[symbol] = lots
        return lots

    def flatten(self, symbol, magic=None):
        pass


def _runner(gw, cap, tmp_path):
    posture = Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager(posture)
    # two full-long books on XAUUSD -> netted exposure stacks well past the cap
    books = [Book(strategy=_Strat(), symbol="XAUUSD", weight=0.5),
             Book(strategy=_Strat(), symbol="XAUUSD", weight=0.5)]
    cfg = RunnerCfg(symbol="PORTFOLIO", exposure_cap=cap,
                    sl_atr_mult=0.0, log_path=tmp_path / "p.jsonl")
    r = PortfolioRunner(gw, books, rm, cfg)
    rm.roll_day(NOW, gw.equity())
    return r


def _last_step(path):
    steps = [json.loads(ln) for ln in path.read_text().splitlines()
             if '"STEP"' in ln]
    return steps[-1]


def test_step_clips_over_aligned_net(tmp_path):
    gw = CapFakeGW(equity=100_000.0)
    _runner(gw, cap=0.5, tmp_path=tmp_path).step()
    # net exposure is clipped: reconcile sees |lots*contract*price/equity| == 0.5
    frac = gw.reconciled["XAUUSD"] * 100.0 * 4000.0 / 100_000.0
    assert abs(abs(frac) - 0.5) < 1e-6
    assert _last_step(tmp_path / "p.jsonl")["fills"]["XAUUSD"]["cap"] is True


def test_step_cap_off_does_not_clip(tmp_path):
    gw = CapFakeGW(equity=100_000.0)
    _runner(gw, cap=0.0, tmp_path=tmp_path).step()
    assert _last_step(tmp_path / "p.jsonl")["fills"]["XAUUSD"]["cap"] is False
```

- [ ] **Step 8: Run the integration tests to verify pass**

Run: `uv run pytest tests/test_exposure_cap.py -q`
Expected: PASS (8 tests). On the flat fake bars `realized_vol == 0`, so each book's leverage is capped at `max_leverage=2`: two full-long books at weight 0.5 each net `0.5 lots` (frac `2.0`), well over the `0.5` cap, so `cap_lots` clips to `0.125 lots` (frac `0.5`) and logs `cap=True`. If this test instead shows `cap=False`, the netted raw exposure came out under the cap — check that the fake bars length (300) exceeds the vol window so `realized_vol` is defined rather than NaN.

- [ ] **Step 9: Wire the config through `run_portfolio.py`**

In `scripts/run_portfolio.py`, the `RunnerCfg(...)` construction (lines 67-71) currently reads:

```python
            cfg=RunnerCfg(symbol="PORTFOLIO",
                          sl_atr_mult=float(cfg.get("sl_atr_mult", 3.0)),
                          tp_atr_mult=float(cfg.get("tp_atr_mult", 0.0)),
                          magic=cfg.get("magic"),
                          log_path=Path("logs/portfolio.jsonl")),
```

Add the `exposure_cap` line:

```python
            cfg=RunnerCfg(symbol="PORTFOLIO",
                          sl_atr_mult=float(cfg.get("sl_atr_mult", 3.0)),
                          tp_atr_mult=float(cfg.get("tp_atr_mult", 0.0)),
                          magic=cfg.get("magic"),
                          exposure_cap=float(cfg.get("exposure_cap", 0.0)),
                          log_path=Path("logs/portfolio.jsonl")),
```

- [ ] **Step 10: Add `exposure_cap` to `config/portfolio.yaml`**

Add a top-level key near `sl_atr_mult`/`tp_atr_mult` (after the `tp_atr_mult` line, before `books:`):

```yaml
exposure_cap: 0.5     # cap |net directional exposure| per symbol as a leverage
                      # multiple of equity (net_lots*contract*price/equity). Bounds
                      # books stacking the same direction; 0 = off. Backtest
                      # (research_portfolio_overlay.py): 0.5 lifts Sharpe, cuts
                      # turnover ~30%, small return give-up. Provisional (1 month).
```

- [ ] **Step 11: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — entire suite green, output pristine.

- [ ] **Step 12: Commit**

```bash
git add src/mt5_trader/live/runner.py scripts/run_portfolio.py \
        config/portfolio.yaml tests/test_exposure_cap.py
git commit -m "feat(portfolio): net-exposure cap (clip stacked book targets, 0.5)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Deploy (after merge)

Not a code step — operator action. On the VPS (`ssh mt5-vps`):
1. `cd Desktop\MT5_Trader; git pull`
2. **Relaunch the portfolio bot** via the `MT5Portfolio` scheduled task to load the new `portfolio.yaml` (`Stop-ScheduledTask MT5Portfolio; Stop-Process` any stray `run_portfolio`; `Start-ScheduledTask MT5Portfolio`). Passive + tick bots untouched.
3. Confirm `logs\portfolio.jsonl` STEP events now carry a `cap` flag per symbol; watch for `"cap": true` when books align.

## Self-Review

- **Spec coverage:** `cap_lots` pure helper (Steps 1-4) ✓; `RunnerCfg.exposure_cap` default 0.0 (Step 5) ✓; `PortfolioRunner.step` applies after vol_halt, before reconcile, logs `cap` (Step 6) ✓; per-symbol via `net.items()` loop ✓; `run_portfolio.py` + `portfolio.yaml` at 0.5 (Steps 9-10) ✓; tests for clip/sign/below-cap/cap-0/zero-equity-price + step integration (Steps 1, 7) ✓; reopen_fade/kill/vol_guard/single-book runners untouched ✓.
- **Placeholder scan:** none — every step shows full code/commands.
- **Type consistency:** `cap_lots(lots, contract_size, price, equity, cap) -> (float, bool)` and `exposure_cap: float` used identically in helper, RunnerCfg, step wiring, run_portfolio, and all tests. `fills[sym]["cap"]` bool key consistent between step and the integration test.
