# Design: Net-exposure cap for the portfolio runner

Date: 2026-06-18
Status: approved (design); implementation pending
Scope: `PortfolioRunner` (`live/runner.py`), `RunnerCfg`, `run_portfolio.py`,
`config/portfolio.yaml`, and a new test file. No strategy, sizing-math, kill,
vol_guard, blackout, or passive-book changes.

## Problem

The portfolio runs six books, all directional on XAUUSD. Each book emits a
signed target in lots; `PortfolioRunner.step` sums them (`net[sym] += lots`) and
reconciles to the net. When books **disagree** the net partially cancels — the
intended diversification. When they **align** (e.g. the trend books all short),
their sizes **stack** into one oversized directional bet with no ceiling.

Observed 2026-06-17: donchian and vwap_trend both went short into a gold
down-move, netting ~−0.10 lots; gold V-reversed at the 22:00 UTC reopen and the
stacked short gave back ~$420 of intraday gains. The kill switch (−4% daily) is
far too coarse to bound this; nothing limits net directional concentration.

## Decision (from research)

Add a **net-exposure cap**: clip each symbol's summed target so the directional
exposure cannot exceed a configured ceiling, measured as a **leverage multiple
of equity** (`frac = net_lots * contract_size * price / equity`). This is the
same unit the vol-targeted overlay backtest used.

Backtest (`scripts/research_portfolio_overlay.py`, vol-targeted, ~1 month real
feed): net_frac ran −1.13…+0.91, exceeding 0.5 on 22% of bars. `CAP 0.5` lifted
Sharpe 5.88→6.05 and cut turnover 67→47 (~30% less churn) for a small return
give-up (+4.85%→+4.52%); maxDD ~unchanged. Tighter caps (0.4/0.3) cost too much
return. **Chosen value: 0.5.** Honest framing: the win is risk-adjusted return +
lower churn, not a dramatic drawdown cut.

Per-symbol cap (not portfolio-total): XAUUSD dominates and no book trades XAGUSD
directionally (`ratio_mr` has `trade_symbol2: false`), so a per-symbol XAU cap is
effectively the portfolio cap, and it generalizes if a real second-symbol book
is added later.

## Components

### 1. `cap_lots` — pure helper in `live/runner.py`

Module-level pure function, beside `chandelier_sl` / `protective_levels` /
`pair_leg_lots` (same convention: pure, unit-tested in isolation).

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

### 2. `PortfolioRunner.step` wiring

In the per-symbol loop (`for sym, lots in net.items()`), after the `vol_halt`
zeroing and after `price` is read, before the sl/tp + reconcile:

```python
            price = float(bars["close"][-1])
            contract = self._cache.spec(sym)["contract_size"]
            lots, capped = cap_lots(lots, contract, price, equity, self.cfg.exposure_cap)
```

Add `"cap": capped` to that symbol's `fills` dict so a binding cap is visible in
the STEP log. `vol_halt` still wins (it zeros `lots` first; `cap_lots` leaves 0
untouched). The cap acts on the *net* across books, never per book.

### 3. `RunnerCfg`

New field `exposure_cap: float = 0.0` (0 = off, matching the `sl_atr_mult`
disabled-by-zero convention). Only `PortfolioRunner` reads it; `LiveRunner` /
`PairRunner` are single-book and unaffected.

### 4. `run_portfolio.py` + `config/portfolio.yaml`

- `run_portfolio.py`: pass `exposure_cap=float(cfg.get("exposure_cap", 0.0))`
  into `RunnerCfg` (alongside the existing `sl_atr_mult`/`tp_atr_mult`/`magic`).
- `portfolio.yaml`: add top-level `exposure_cap: 0.5` with a comment explaining
  the unit (leverage multiple of equity) and that it bounds the netted
  directional position across books.

## Tests — `tests/test_exposure_cap.py`

`cap_lots` (pure):
- exposure above cap → clipped so `|lots*contract*price/equity| == cap` (within
  fp tolerance), sign preserved (long stays long, short stays short).
- exposure below cap → returned unchanged, `bound is False`.
- `cap = 0` → off (unchanged, `bound is False`) regardless of exposure.
- `equity <= 0` or `price <= 0` → safe no-op (`bound is False`).

`PortfolioRunner.step` (FakeGW, mirrors `test_kill_flatten`): two books whose
summed net exposure exceeds the cap → reconcile receives the clipped lots and the
STEP log records `"cap": true` for the symbol. A non-aligned net under the cap →
unclipped, `"cap": false`.

## Out of scope

- reopen_fade book (shelved: the overlay showed it cancels the net-right trend
  books rather than complementing them).
- Kill switch, vol_guard, blackout, per-book sizing, passive book.
- Portfolio-total (cross-symbol) cap — per-symbol suffices while XAU dominates.

## Risks

- **~1 month, one regime** (gold trended up → trend books net-right). The 0.5
  value is provisional; re-confirm on more data. The cap can only *reduce*
  return when books are right together — it is a risk guardrail, not an alpha
  source.
- **Value is in live units** (vol-targeted leverage multiple), pinned by the
  vol-targeted overlay sim — not the earlier signal-space "0.6". Deploy 0.5.
- **maxDD barely moved** in the backtest; do not oversell as a drawdown fix. Its
  measured benefit is Sharpe + turnover reduction.

## Workflow

Branch `feat/exposure-cap` → TDD `cap_lots` → wire `PortfolioRunner` + config →
tests → full suite → PR. Deploy: VPS `git pull`, relaunch the portfolio bot
(`MT5Portfolio` scheduled task) to load the new `portfolio.yaml`. Passive + tick
bots untouched.
