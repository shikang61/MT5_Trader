# Spec: CUSUM change-point regime gate (vs efficiency-ratio)

Status: design. Author: QR/QD. Date: 2026-06-24.

> **OUTCOME (2026-06-24): NOT DEPLOYED — fails the plateau gate.**
> Head-to-head (none/ER/CUSUM): donchian raw best (both gates hurt); vwap ER best
> (Sh 1.84 > CUSUM 1.67); london has ONE cell (k0.25/h3.0, Sh 4.50) beating raw+ER
> but isolated — neighbors collapse (h5.0→−1.97, k0.5→0.30), the overfit signature
> that shelved [[macro-gate-shelved]]. No book clears "beat ER on Sharpe+maxDD on a
> plateau." `cusum_regime` + research script KEPT as research tooling; ER stays the
> only live gate. Task 3 (deploy) cancelled. 5th dead reversal/regime lever — see
> [[profit-ratchet-dead]].

## Goal & hypothesis

Add a **change-point (CUSUM) regime detector** as an alternative to the existing
efficiency-ratio (ER) gate, and test whether it gates the trend books
(`donchian`, `vwap_trend`, `london_orb`) **better** than ER — i.e. turns them off
in chop / when a trend dies, raising gated Sharpe and trimming drawdown.

This is a **regime gate (book on/off), not a per-trade stop-cut** — deliberately
outside the dead protect-giveback family ([[profit-ratchet-dead]]). It must beat
**ER**, not merely the ungated baseline; a tie loses (ER already exists,
`features/regime.py`).

**Prior:** reversal/regime detection is hard and most variants here have died at
the gate. CUSUM is the cheapest principled change-point method (online, causal),
so it is the right first probe — but the skeptical prior holds: likely dropped at
the gate, which is a successful outcome.

## Mechanism

A causal two-sided CUSUM on **volatility-standardized returns** detects a shift in
drift (trend onset/reversal); absence of a recent detection = chop. Output mirrors
`regime_series`: a per-bar array of `{TREND_UP=+1, RANGE=0, TREND_DOWN=-1}`, so the
books gate identically (`regime != RANGE`).

```
# coarsened, causal (anchor sampling to START, like regime_series)
z[t]   = ret[t] / rolling_std(ret, vol_window)        # standardized return
g_pos  = max(0, g_pos + z[t] - drift_k)               # upward-drift accumulator
g_neg  = max(0, g_neg - z[t] - drift_k)               # downward-drift accumulator
if g_pos > threshold_h:  state, g_pos, g_neg = +1, 0, 0   # up change-point
elif g_neg > threshold_h: state, g_pos, g_neg = -1, 0, 0  # down change-point
else if `hold` bars elapsed since the last detection: state = 0   # drift died -> chop
```

- `drift_k` = slack (drift below this is noise, accumulators bleed to 0); `threshold_h`
  = detection sensitivity (higher = fewer, surer change-points).
- Causal by construction (online recursion) → backtest == live, no lookahead.
- Coarsened sampling (anchored to the series start, as `regime_series` does) so a
  past bar's regime is independent of future bars and M1 microstructure noise
  doesn't trigger false drifts.
- Disabled / degenerate params → return all-`RANGE`? No — return all-`TREND` (mask
  all True) so a "gate off" is a no-op passthrough; the books decide via
  `regime_method`.

### Interface (drop-in beside ER)

```
def cusum_regime(bars: pl.DataFrame, drift_k: float = 0.5, threshold_h: float = 5.0,
                 vol_window: int = 60, hold: int = 30, coarsen: int = 15) -> np.ndarray:
    """Per-bar regime {+1 trend up, 0 range, -1 trend down} from a causal two-sided
    CUSUM on vol-standardized returns. Same shape/semantics as regime_series so a
    book can gate on `regime != RANGE` with either method."""
```

Lives in `src/mt5_trader/features/regime.py` next to `regime_series`. Pure, causal,
no MT5/IO.

## Research phases (gated)

- **Phase 0 — head-to-head (decisive).** `scripts/research_cusum_regime.py`: for each
  trend book on the real feed, build the **raw (ungated)** signal, then compare three
  gates — *none* / *ER* (`regime_series`) / *CUSUM* (`cusum_regime`, sweep
  `drift_k × threshold_h`) — via `engine.run` (mask the signal to 0 where
  `regime == RANGE`). Report return / Sharpe / maxDD / turnover per gate.
  **Gate criterion:** keep CUSUM for a book only if it **beats ER on Sharpe AND maxDD
  on a plateau** (neighboring `k,h` agree). Report + sign-off.
- **Phase 1 — deploy** (only books that clear): add a `regime_method: "er" | "cusum"`
  param to the gating path (and the CUSUM params) so a book opts in via
  `config/portfolio.yaml`; default `"er"` (no behavior change).

## Testing

- `cusum_regime` (pure): a constructed persistent up-drift series → mask mostly
  `+1`; a choppy zero-drift series → mostly `0`; a down-drift → `-1`; causal (regime
  at bar t unchanged when later bars are appended); degenerate params → all-`TREND`
  passthrough.
- Head-to-head harness: ER path reproduces the books' existing `regime_filter`
  result; CUSUM path runs and is comparable.

## Scope / non-goals

- Not a new entry signal, not a per-trade exit/cut, not BOCPD/HMM (deferred — only
  if CUSUM shows life).
- Compared against ER, not just ungated. Default stays ER.
- Phase 1 conditional on the Phase-0 gate + user sign-off.

## Related

[[profit-ratchet-dead]] (dead reversal-cut family — this is a *gate*, not a cut),
[[strategy-robustness-levers]], [[macro-gate-shelved]] (another gate that failed
the placebo — hold CUSUM to the same multiple-comparisons skepticism),
[[data-sources-scaling]] (order-flow is the other reversal-detection frontier —
separate doc).
