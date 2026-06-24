# Rebalance Deadband + Richer ERROR Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **OUTCOME (2026-06-24):** Tasks 1–4 executed. At the Task 5 gate the deadband
> was **dropped** — discrete {-1,0,1} signals make a signal deadband a no-op, and
> real spread cost is ~$31/week (the "-755" was an attribution artifact, not
> cost). Tasks 1–2 (deadband helper + engine wiring) were **reverted**; Tasks 6–7
> **cancelled**. Tasks 3–4 (#3 error context) shipped and kept.

**Goal:** Cut execution cost via a research-gated per-book rebalance deadband, and make order errors self-diagnosing.

**Architecture:** A pure `apply_deadband`/`deadband_series` shared by the backtest engine and the live runner (research-prod parity); a research sweep + decision gate before any live deploy; plus `MT5Error` message enrichment and a deduped, non-silent surfacing of swallowed invalid-stops.

**Tech Stack:** Python 3.11, polars, numpy, pytest, `uv run`. MetaTrader5 package is win32-only (mocked in tests).

## Global Constraints

- TDD: every behavior change has a failing test first. Run `uv run pytest` for the suite.
- Deadband acts on the **per-book signal ∈[-1,1]** (the engine's `pos`), before risk-sizing/weighting and before netting. Same quantity in engine and runner.
- Deadband rule: keep held unless `|target - held| > band`; `band <= 0` disables. Default 0 everywhere (no behavior change until bands are set post-gate).
- `MT5Gateway` stays import-safe on macOS (no `mt5` calls at import); tests monkeypatch `mt5_gateway.mt5` with a fake.
- Order errors are per-symbol on the netted position → the book is not knowable; surface symbol/magic/action/retcode only.
- **Phase 2 (Tasks 6–7) is gated:** do not start until the Task 5 table is reviewed and the user signs off on which bands to deploy.

---

### Task 1: `apply_deadband` + `deadband_series` (shared pure helpers)

**Files:**
- Create: `src/mt5_trader/execution.py`
- Test: `tests/test_deadband.py`

**Interfaces:**
- Produces: `apply_deadband(target: float, held: float, band: float) -> float`; `deadband_series(pos: np.ndarray, band: float) -> np.ndarray`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_deadband.py
import numpy as np

from mt5_trader.execution import apply_deadband, deadband_series


def test_below_band_holds():
    assert apply_deadband(0.05, 0.0, 0.1) == 0.0


def test_above_band_moves():
    assert apply_deadband(0.2, 0.0, 0.1) == 0.2


def test_exact_boundary_holds():
    # |0.1 - 0| == band -> not strictly greater -> hold
    assert apply_deadband(0.1, 0.0, 0.1) == 0.0


def test_band_zero_passthrough():
    assert apply_deadband(0.03, 0.0, 0.0) == 0.03


def test_negative_band_passthrough():
    assert apply_deadband(0.03, 0.0, -1.0) == 0.03


def test_sign_flip_moves():
    assert apply_deadband(-0.5, 0.5, 0.1) == -0.5


def test_series_holds_subband_wiggle_then_breaks_out():
    pos = np.array([0.0, 0.05, -0.04, 0.06, 0.5, 0.52])
    out = deadband_series(pos, 0.1)
    assert list(out) == [0.0, 0.0, 0.0, 0.0, 0.5, 0.5]
    # turnover (sum |diff|) strictly lower than the raw series
    raw_turn = np.abs(np.diff(pos, prepend=0.0)).sum()
    db_turn = np.abs(np.diff(out, prepend=0.0)).sum()
    assert db_turn < raw_turn


def test_series_band_zero_is_identity():
    pos = np.array([0.0, 0.3, -0.2])
    assert np.array_equal(deadband_series(pos, 0.0), pos)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_deadband.py -q`
Expected: FAIL — `ModuleNotFoundError: mt5_trader.execution` / cannot import names.

- [ ] **Step 3: Write the implementation**

```python
# src/mt5_trader/execution.py
"""Position helpers shared by the backtester and the live runner so a turnover
change is identical in research and production. Pure: no MT5, no I/O."""
from __future__ import annotations

import numpy as np


def apply_deadband(target: float, held: float, band: float) -> float:
    """Hold `held` until `target` moves more than `band` away from it, then snap
    to `target`. `target`/`held` are normalized positions in [-1, 1]; `band` is in
    the same units. `band <= 0` disables (returns `target`)."""
    if band <= 0:
        return target
    return held if abs(target - held) <= band else target


def deadband_series(pos: np.ndarray, band: float) -> np.ndarray:
    """Apply `apply_deadband` sequentially over a position array, carrying the held
    position forward (starts flat). `band <= 0` returns `pos` unchanged."""
    if band <= 0:
        return pos
    out = np.empty_like(pos)
    held = 0.0
    for i in range(len(pos)):
        held = apply_deadband(float(pos[i]), held, band)
        out[i] = held
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_deadband.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/execution.py tests/test_deadband.py
git commit -m "feat(execution): apply_deadband + deadband_series (shared pure helpers)"
```

---

### Task 2: Wire deadband into the backtest engine

**Files:**
- Modify: `src/mt5_trader/backtest/engine.py` (add `deadband` to `BTConfig`; apply in `run`)
- Test: `tests/test_deadband.py` (append)

**Interfaces:**
- Consumes: `deadband_series` from Task 1.
- Produces: `BTConfig.deadband: float = 0.0`; `run()` applies it to `pos`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_deadband.py
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run


def _bars(closes):
    return pl.DataFrame({
        "ts": [f"2026-06-01T00:{i:02d}:00Z" for i in range(len(closes))],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "spread_mean": [0.1] * len(closes),
    })


def test_engine_deadband_reduces_turnover():
    # signal wiggles sub-band around a level then makes a real move
    sig = np.array([0.0, 0.05, -0.04, 0.06, -0.03, 1.0, 0.97, 1.0])
    bars = _bars([100.0 + i * 0.1 for i in range(len(sig))])
    base = run(bars, sig, BTConfig(deadband=0.0))
    band = run(bars, sig, BTConfig(deadband=0.2))
    assert band.metrics["total_turnover"] < base.metrics["total_turnover"]


def test_engine_deadband_zero_matches_default():
    sig = np.array([0.0, 0.3, -0.2, 0.5])
    bars = _bars([100.0, 100.1, 99.9, 100.2])
    a = run(bars, sig, BTConfig(deadband=0.0))
    b = run(bars, sig, BTConfig())
    assert np.array_equal(a.bars["pos"].to_numpy(), b.bars["pos"].to_numpy())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_deadband.py::test_engine_deadband_reduces_turnover -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'deadband'`.

- [ ] **Step 3: Add the `deadband` field to `BTConfig`**

In `src/mt5_trader/backtest/engine.py`, add to the `BTConfig` dataclass (after `atr_window`):

```python
    atr_window: int = 60       # ATR lookback for the stop distance
    deadband: float = 0.0      # hold position until |target-held| > deadband (turnover cut); 0 = off
```

- [ ] **Step 4: Apply the deadband in `run` and import the helper**

Add the import near the top of `engine.py` (with the other `..` imports):

```python
from ..execution import deadband_series
```

In `run`, immediately after the position is constructed:

```python
    pos = np.empty(n)
    pos[0] = 0.0
    pos[1:] = sig[:-1]  # executed one bar after the signal
    pos = deadband_series(pos, cfg.deadband)   # <-- add this line
```

(Placed before the stop simulation and turnover calc, which both read `pos`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_deadband.py -q`
Expected: PASS (10 passed).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mt5_trader/backtest/engine.py tests/test_deadband.py
git commit -m "feat(engine): apply rebalance deadband to the position series"
```

---

### Task 3: Enrich `MT5Error` with request context (#3a)

**Files:**
- Modify: `src/mt5_trader/live/mt5_gateway.py` (`_send`)
- Test: `tests/test_sltp_clamp.py` (append)

**Interfaces:**
- Produces: `MT5Error` raised by `_send` whose message contains `action`, `symbol`, `magic`, `retcode`; `.retcode` attribute unchanged.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_sltp_clamp.py
def test_send_error_message_carries_request_context(monkeypatch):
    class RejectMT5:
        TRADE_RETCODE_DONE = DONE
        def order_send(self, req):
            return types.SimpleNamespace(retcode=MARKET_CLOSED, comment="closed")
        def last_error(self):
            return (0, "")
    monkeypatch.setattr(gwmod, "mt5", RejectMT5())
    gw = MT5Gateway.__new__(MT5Gateway)
    with pytest.raises(MT5Error) as ei:
        gw._send({"action": 1, "symbol": "XAUUSD", "magic": 1001})
    msg = str(ei.value)
    assert "XAUUSD" in msg and "1001" in msg and str(MARKET_CLOSED) in msg
    assert ei.value.retcode == MARKET_CLOSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sltp_clamp.py::test_send_error_message_carries_request_context -v`
Expected: FAIL — message lacks `XAUUSD`/`1001` (current message only has retcode + comment).

- [ ] **Step 3: Enrich the error message in `_send`**

Replace the current `_send` body with:

```python
    def _send(self, request: dict) -> None:
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = getattr(result, "retcode", None)
            err = MT5Error(
                f"order_send failed: retcode={retcode} "
                f"action={request.get('action')} symbol={request.get('symbol')} "
                f"magic={request.get('magic')} "
                f"comment={getattr(result, 'comment', '')!r} {mt5.last_error()}")
            err.retcode = retcode
            raise err
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sltp_clamp.py::test_send_error_message_carries_request_context -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/mt5_gateway.py tests/test_sltp_clamp.py
git commit -m "feat(gateway): MT5Error carries action/symbol/magic/retcode context"
```

---

### Task 4: Surface swallowed invalid-stops — deduped, non-silent (#3b)

**Files:**
- Modify: `src/mt5_trader/live/mt5_gateway.py` (`set_position_sltp`: add `_note` + per-ticket dedup of failed SLTP)
- Modify: `src/mt5_trader/live/runner.py` (`PortfolioRunner.__init__`: route gateway notes to `_log`)
- Test: `tests/test_sltp_clamp.py` (append), `tests/test_step_isolation.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `MT5Gateway.on_event` (optional callable, default absent); `set_position_sltp` calls it once per *changed* failing stop; `PortfolioRunner` sets `gw.on_event = self._log`.

**Why dedup:** a swallowed SLTP never updates `p.sl`, so without dedup the same failing stop re-sends (and would re-log) every cycle — the 158-line storm. The per-ticket `_sltp_failed` memo suppresses re-send + re-note until the requested stop changes or one succeeds.

- [ ] **Step 1: Write the failing gateway tests**

```python
# append to tests/test_sltp_clamp.py
def test_swallowed_invalid_stops_notes_once_and_does_not_resend(monkeypatch):
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=INVALID_STOPS)
    gw = _gw(monkeypatch, fake)
    events = []
    gw.on_event = lambda **kw: events.append(kw)
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)   # fails invalid-stops, swallowed
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)   # same stop -> no re-send, no re-note
    sltp = [r for r in fake.sent if r["action"] == FakeMT5.TRADE_ACTION_SLTP]
    assert len(sltp) == 1                 # second call did not re-send
    assert len(events) == 1               # noted exactly once
    assert events[0]["symbol"] == "XAUUSD" and events[0]["retcode"] == INVALID_STOPS


def test_changed_failing_stop_notes_again(monkeypatch):
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=INVALID_STOPS)
    gw = _gw(monkeypatch, fake)
    events = []
    gw.on_event = lambda **kw: events.append(kw)
    gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)
    gw.set_position_sltp("XAUUSD", sl=4250.0, magic=7)   # different stop -> note again
    assert len(events) == 2


def test_non_invalid_rejection_still_raises_in_sltp(monkeypatch):
    fake = FakeMT5([Pos(1, 0.02, SELL, magic=7)], reject=MARKET_CLOSED)
    gw = _gw(monkeypatch, fake)
    with pytest.raises(MT5Error):
        gw.set_position_sltp("XAUUSD", sl=4200.0, magic=7)
```

Note: `test_sltp_invalid_stops_does_not_abort_cycle` (already in the file) still passes — the swallow remains; only the dedup/note is added.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sltp_clamp.py -q`
Expected: FAIL — `on_event` not called (no `_note`); no dedup (second call re-sends).

- [ ] **Step 3: Add `_note` and dedup to the gateway**

Add a helper method to `MT5Gateway` (near `_send`):

```python
    def _note(self, **kw) -> None:
        """Emit a structured event to an optional sink (set by the runner). No-op
        if unset — keeps the gateway import-safe and usable without a logger."""
        cb = getattr(self, "on_event", None)
        if cb is not None:
            cb(**kw)
```

Replace the `set_position_sltp` per-position block (the `if abs(p.sl ...): try: ... except ...`) with:

```python
        if not hasattr(self, "_sltp_failed"):
            self._sltp_failed = {}   # ticket -> (sl, tp) that last failed invalid-stops
        for p in self._mine(mt5.positions_get(symbol=symbol), magic):
            side = 1.0 if p.type == mt5.POSITION_TYPE_BUY else -1.0
            psl = self._round_price(symbol, clamp_sl(side, sl, tick.bid, tick.ask, dist))
            if abs(p.sl - psl) <= 1e-9 and abs(p.tp - tp) <= 1e-9:
                continue
            if self._sltp_failed.get(p.ticket) == (psl, tp):
                continue  # this exact stop already failed invalid-stops; don't re-send/re-note
            try:
                self._send({"action": mt5.TRADE_ACTION_SLTP, "symbol": symbol,
                            "position": p.ticket, "sl": psl, "tp": tp})
                self._sltp_failed.pop(p.ticket, None)
            except MT5Error as e:
                # A protective-stop modify the broker rejects as an invalid stop
                # (too close / through the freeze level) must not abort the trading
                # cycle — clamp_sl already widened it. Memo + surface once; re-raise
                # anything else (a real broker fault).
                if getattr(e, "retcode", None) != mt5.TRADE_RETCODE_INVALID_STOPS:
                    raise
                self._sltp_failed[p.ticket] = (psl, tp)
                self._note(event="ERROR", symbol=symbol, retcode=e.retcode, error=repr(e))
```

(The `tick`/`dist`/`tp` lines above this block are unchanged.)

- [ ] **Step 4: Run gateway tests to verify they pass**

Run: `uv run pytest tests/test_sltp_clamp.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing runner-wiring test**

```python
# append to tests/test_step_isolation.py
def test_portfolio_runner_routes_gateway_events_to_log(tmp_path):
    gw = FaultyGW(bad_symbol="NONE")           # nothing fails; we only check wiring
    r = _runner(gw, tmp_path)
    assert gw.on_event == r._log
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_step_isolation.py::test_portfolio_runner_routes_gateway_events_to_log -v`
Expected: FAIL — `AttributeError: 'FaultyGW' object has no attribute 'on_event'`.

- [ ] **Step 7: Route gateway notes to the runner log**

In `PortfolioRunner.__init__` (`src/mt5_trader/live/runner.py`), after `super().__init__(...)`:

```python
        self.books = books
        self.gw.on_event = self._log    # <-- add: gateway surfaces deduped errors here
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_step_isolation.py tests/test_sltp_clamp.py -q`
Expected: PASS.

- [ ] **Step 9: Full suite + commit**

```bash
uv run pytest -q
git add src/mt5_trader/live/mt5_gateway.py src/mt5_trader/live/runner.py tests/test_sltp_clamp.py tests/test_step_isolation.py
git commit -m "feat(live): surface swallowed invalid-stops (deduped) instead of silent"
```

---

### Task 5: Research sweep `research_deadband.py` + DECISION GATE

**Files:**
- Create: `scripts/research_deadband.py`

**Interfaces:**
- Consumes: `BTConfig`/`run` (with `deadband`), `build_bars`/`bars_per_year`, `REGISTRY`, `spread_bars`.

This task produces a table, not a unit test (exploratory research script, matching the existing `scripts/research_*.py`). **STOP after running it and report the table to the user. Do not start Task 6 until the user signs off on which bands to deploy.**

- [ ] **Step 1: Write the research script**

```python
#!/usr/bin/env python
"""RESEARCH (not a live component): does a per-book rebalance deadband cut cost
without giving up edge? Sweeps `deadband` per portfolio.yaml book on the real feed
and reports return / Sharpe / maxDD / turnover vs the band=0 baseline.

    uv run scripts/research_deadband.py
"""
from __future__ import annotations

import yaml

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.pipeline.data import bars_per_year, build_bars
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

TF_EVERY = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h"}
BANDS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3]
SL_ATR_MULT, ATR_WINDOW, SLIPPAGE_BPS = 8.0, 60, 0.5   # mirror the live portfolio stop + cost


def book_bars(book: dict):
    every = TF_EVERY[book.get("timeframe", "M1")]
    bars = build_bars(book["symbol"], every).sort("ts")
    if book.get("symbol2"):
        bars = spread_bars(bars, build_bars(book["symbol2"], every).sort("ts"),
                           float(book.get("beta", 1.0)))
    return bars, bars_per_year(every)


def main():
    with open("config/portfolio.yaml") as f:
        books = yaml.safe_load(f)["books"]
    for book in books:
        name = book["strategy"]
        bars, bpy = book_bars(book)
        sig = REGISTRY[name](**(book.get("params", {}) or {})).signal(bars)
        print(f"\n=== {name}@{book['symbol']} {book.get('timeframe','M1')} "
              f"(weight {book.get('weight',1.0)}) ===")
        print(f"{'band':>6}{'return%':>10}{'sharpe':>9}{'maxDD%':>9}{'turnover':>10}{'vs base':>9}")
        base_turn = None
        for band in BANDS:
            cfg = BTConfig(deadband=band, sl_atr_mult=SL_ATR_MULT,
                           atr_window=ATR_WINDOW, slippage_bps=SLIPPAGE_BPS,
                           bars_per_year=bpy)
            m = run(bars, sig, cfg).metrics
            base_turn = base_turn if base_turn is not None else m["total_turnover"]
            cut = (m["total_turnover"] / base_turn - 1) * 100 if base_turn else 0.0
            print(f"{band:>6.2f}{m['total_return']*100:>10.2f}{m['sharpe']:>9.2f}"
                  f"{m['max_drawdown']*100:>9.2f}{m['total_turnover']:>10.2f}{cut:>8.0f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the sweep**

Run: `uv run scripts/research_deadband.py`
Expected: a per-book table over bands 0–0.3. (If `build_bars` errors on missing data, fall back to `--data data/real` conventions used by other research scripts; the real feed must be present locally.)

- [ ] **Step 3: Commit the script**

```bash
git add scripts/research_deadband.py
git commit -m "feat(research): per-book rebalance-deadband sweep"
```

- [ ] **Step 4: DECISION GATE — report + wait**

Summarize per book: does a band cut turnover materially while keeping/raising Sharpe, on a plateau (neighboring bands agree)? Recommend a band per book (likely 0 for the fades, >0 for VwapTrend/Donchian). **Present to the user and wait for sign-off before Task 6.**

---

### Task 6: Wire the deadband into the live runner (per-book) — POST-GATE

**Files:**
- Modify: `src/mt5_trader/live/runner.py` (`Book` dataclass + `Book.targets`)
- Modify: `scripts/run_portfolio.py` (`load_books`: read `deadband`)
- Test: `tests/test_step_isolation.py` (append)

**Interfaces:**
- Consumes: `apply_deadband` (Task 1).
- Produces: `Book.deadband: float = 0.0`; `Book.targets` deadbands `sig` before sizing; `load_books` passes `deadband` from yaml.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_step_isolation.py
from mt5_trader.live.runner import Book


class _StepSig:
    """Returns a preset signal whose last value we control per call."""
    def __init__(self, value):
        self.value = value
    def signal(self, bars):
        return np.full(len(bars), self.value)


def test_book_deadband_holds_subband_signal(tmp_path):
    rm = RiskManager(Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3))
    rm.roll_day(NOW, 100_000.0)
    gw = FaultyGW(bad_symbol="NONE")
    book = Book(strategy=_StepSig(1.0), symbol="XAUUSD", weight=1.0,
                timeframe="M1", deadband=0.2)
    # first decision: sig 1.0 -> held becomes 1.0
    book.targets(_cache_for(gw), 100_000.0, rm, NOW, 240)
    assert book._held_sig == 1.0
    # a sub-band move (to 0.95) is held at 1.0, not adopted
    book.strategy.value = 0.95
    book._last_bar_ts = None      # force recompute (new bar)
    book.targets(_cache_for(gw), 100_000.0, rm, NOW, 240)
    assert book._held_sig == 1.0
```

Add this helper near the top of `tests/test_step_isolation.py` (a `_BarCache`-shaped object the `Book` can call):

```python
from mt5_trader.live.runner import _BarCache

def _cache_for(gw):
    return _BarCache(gw, lookback=3000)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_step_isolation.py::test_book_deadband_holds_subband_signal -v`
Expected: FAIL — `Book.__init__` has no `deadband`; `_held_sig` missing.

- [ ] **Step 3: Add fields to `Book` and the import**

Add the import at the top of `runner.py` (with the existing `.mt5_gateway` import line area):

```python
from ..execution import apply_deadband
```

In the `Book` dataclass, add two fields (after `timeframe`):

```python
    timeframe: str = "M1"
    deadband: float = 0.0          # hold sig until |sig-held| > deadband; 0 = off
    _held_sig: float = 0.0         # last band-output signal (state across bars)
```

- [ ] **Step 4: Apply the deadband in `Book.targets`**

In `Book.targets`, immediately after `sig = float(self.strategy.signal(closed)[-1])`:

```python
        sig = float(self.strategy.signal(closed)[-1])
        sig = apply_deadband(sig, self._held_sig, self.deadband)   # <-- add
        self._held_sig = sig                                       # <-- add
```

- [ ] **Step 5: Read `deadband` from yaml in `load_books`**

In `scripts/run_portfolio.py`, add to the `Book(...)` call in `load_books`:

```python
             timeframe=b.get("timeframe", "M1"),
             deadband=float(b.get("deadband", 0.0)))
```

- [ ] **Step 6: Run tests + full suite**

Run: `uv run pytest tests/test_step_isolation.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mt5_trader/live/runner.py scripts/run_portfolio.py tests/test_step_isolation.py
git commit -m "feat(live): per-book rebalance deadband on the signal (default off)"
```

---

### Task 7: Apply the passing bands to portfolio.yaml — POST-GATE

**Files:**
- Modify: `config/portfolio.yaml` (add `deadband:` to the books that passed the gate)

- [ ] **Step 1: Set the bands**

For each book the user approved in Task 5, add `deadband: <value>` to its block, with a one-line comment citing the sweep result (return/Sharpe/turnover delta), matching the file's existing annotation style. Leave non-approved books without the key (defaults to 0).

- [ ] **Step 2: Validate config loads and weights still sum ~1**

Run: `uv run python -c "from scripts.run_portfolio import load_books; from pathlib import Path; bks,_=load_books(Path('config/portfolio.yaml')); print([(b.strategy.__class__.__name__, b.deadband) for b in bks])"`
Expected: prints each book with its deadband; no `SystemExit` on weight sum.

- [ ] **Step 3: Full suite + commit**

```bash
uv run pytest -q
git add config/portfolio.yaml
git commit -m "config(portfolio): enable rebalance deadband on books that cleared the sweep"
```

Deploy (after merge) per the live-runtime runbook: VPS `git pull` + restart `MT5Portfolio` via `Stop+Start-ScheduledTask`.

---

## Self-Review

**Spec coverage:**
- #1 deadband mechanism (parity fn) → Task 1. Engine wiring → Task 2. Research + gate → Task 5. Live wiring (per-book, before sizing/netting) → Task 6. Deploy config → Task 7. ✓
- #3a error context → Task 3. ✓
- #3b surface swallowed invalid-stops (deduped, non-silent) → Task 4. ✓
- #4 dropped → no task (correct). ✓

**Placeholder scan:** none — all steps carry concrete code/commands.

**Type consistency:** `apply_deadband(target, held, band)` and `deadband_series(pos, band)` used identically in Tasks 1/2/6. `BTConfig.deadband`, `Book.deadband`, `Book._held_sig`, `MT5Gateway.on_event`, `_sltp_failed` names consistent across tasks. Metrics keys (`total_return`, `sharpe`, `max_drawdown`, `total_turnover`) match `backtest/metrics.py:summary`.
