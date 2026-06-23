# Spec: Rebalance deadband (cost) + richer ERROR context

Status: design. Author: QR/QD. Date: 2026-06-24.

Two independent improvements found while reviewing the 2026-06-23 live session
(the 10016 `Invalid stops` storm investigation):

1. **#1 Rebalance deadband** — cut execution cost (turnover). Research-gated:
   backtest before any live change.
2. **#3 ERROR context** — make order errors self-diagnosing. Platform fix, TDD,
   no backtest.

Dropped from the original batch: **#4 per-book/M1 stop floor** — investigation
showed the disaster stop is per-symbol on the net position, sized on the coarsest
(M5) ATR, ~$25.8 / 61bp wide, trailing correctly. The 0.22 "tight stop" was price
*rallying into a correctly-trailed stop*, not a tight stop; the 10016 was pure
SLTP-modify mechanics, already fixed by the clamp + swallow shipped in
`fix/sltp-10016-and-step-isolation`. Nothing to fix.

---

## #1 Rebalance deadband

### Motivation

Live week Jun16–23 gross **+1322.72**, cost/slippage/swap gap **−755 (57% of
gross)**. Swap was 0 → it is spread + churn. The backtest engine charges
`turnover * (spread_frac/2 + slippage_bps)` where `turnover = |diff(pos)|`
(`backtest/engine.py`), so any change that reduces how often the position moves
cuts cost identically in backtest and live. Worst offender:
`VwapTrend` turnover 1.08 *and* gross −80.93; `DonchianBreakout` turnover 0.87.

### Mechanism (parity-critical)

One shared pure function used by **both** the engine and the live runner so
backtest == live:

```
def apply_deadband(target: float, held: float, band: float) -> float:
    """Hold the current position until the target moves more than `band`.
    target/held are normalized positions in [-1, 1]; band is in the same units.
    band <= 0 disables (returns target unchanged)."""
    if band <= 0:
        return target
    return held if abs(target - held) <= band else target
```

- Applies on the **normalized position / target_frac** (∈[-1,1]) — unit-consistent
  on both sides (engine `pos`, live `target_frac`).
- Rule: keep `held` unless `|target - held| > band`, then move fully to `target`
  (classic deadband / hysteresis). No partial / sticky move — keep it simple.
- Stateful across bars (needs the previously-held position), so the engine applies
  it as a sequential pass over the position series, and the runner applies it to
  each book's target against that book's last held target.
- **Per-book, before netting.** The engine backtests each book standalone, so the
  band is calibrated on a per-book position series. To match, the runner applies
  the band to each book's `target_frac` *before* the per-symbol netting/reconcile
  — never on the post-net symbol target. This preserves research-prod parity;
  the existing lot-step threshold in `reconcile` is a separate, downstream guard.

### Phase 1 — research (gate before any deploy)

`scripts/research_deadband.py` (mirrors existing `research_*.py`):

- Data: the real broker feed `data/real` (per the real-spread default), per book in
  `config/portfolio.yaml`.
- Sweep `band ∈ {0, 0.05, 0.1, 0.15, 0.2, 0.3}` per book (0 = current baseline).
- Report per (book, band): annualized return, Sharpe, turnover, cost, maxDD —
  as a delta vs the band=0 baseline.

**Decision gate.** Deploy a band for a book only if ALL hold:
- cost falls materially (turnover down), AND
- net Sharpe is kept or improved vs baseline, AND
- it sits on a **plateau** (neighboring bands agree — not a single hot cell).

Expectation: VwapTrend / Donchian are the candidates; the fades barely trade live
(SweepFade 17 bars/wk, AsianSweepFade fired once) so they should land at band≈0.

The Phase-1 numbers are reported back to the user. **No live change without
sign-off on the table.**

### Phase 2 — deploy (only books that passed the gate)

- Wire `apply_deadband` into the runner's per-book target step and the engine's
  position pass.
- Per-book `deadband:` param in `config/portfolio.yaml` (default 0 = off), read
  through the existing book-config path.

### Testing (#1)

- `apply_deadband`: pure unit tests — below-band hold, above-band move, band<=0
  passthrough, exact-boundary (== band → hold), sign flips.
- Engine parity: a position series with sub-band wiggles yields lower turnover
  with a band than without, and identical positions when band=0.
- Runner: a book whose target wiggles below band does not re-reconcile.

---

## #3 Richer ERROR context

### Motivation

The 158 `10016` errors on 2026-06-23 logged only the exception string
(`{"event":"ERROR","error":"MT5Error(...)"}`) — no symbol/magic/retcode — so
attributing the storm needed cross-referencing STEP fills by hand. Order errors
are per-**symbol** on the netted position, so the specific *book* is not knowable
(magic 1001 = whole portfolio); symbol / magic / action / retcode is the
attainable and sufficient context.

### Design

1. **`mt5_gateway._send`** — build the `MT5Error` message from the request it is
   sending: `action, symbol, magic, retcode, comment`. The request dict already
   carries all of these. Every error (swallowed or bubbled) then carries context
   in its string, so both the `resilient_loop` logger and the per-symbol catch
   show it. `retcode` stays attached as an attribute (already added). No control-
   flow change.
2. **Runner per-symbol reconcile catch** — the swallowed invalid-stops branch
   currently logs nothing (silent). Emit a structured `ERROR` log
   (`event=ERROR, symbol, retcode, error`) so a clamped-but-still-rejected stop
   is visible rather than invisible.

### Testing (#3)

- `_send` raises an `MT5Error` whose message contains the action, symbol, magic,
  and retcode (assert against the fake `mt5` order_send).
- The swallowed invalid-stops path writes an `ERROR` log line carrying the symbol
  and retcode (assert the log, cycle still not aborted).

---

## Scope / non-goals

- No new alpha. #1 only removes turnover on existing edges; #3 is observability.
- #4 dropped (see top). Give-back / faster-exit ideas are separate, future,
  strategy-level research — not in this batch.
- #1 Phase 2 is conditional on Phase-1 results + user sign-off.

## Related

[[strategy-robustness-levers]], [[backtest-data-and-spread]],
[[fast-vol-defense-dead]] (prior de-sizing levers that failed — deadband is a
turnover cut, not a vol defense, so a different hypothesis).
