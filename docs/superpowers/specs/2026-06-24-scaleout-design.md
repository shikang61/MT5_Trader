# Spec: Scale-out (partial profit-banking)

Status: design. Author: QR/QD. Date: 2026-06-24.

## Goal

Bank part of a winning trade as it runs, to lock in profit progressively without
truncating the trend. Once open profit reaches `T`×ATR, close a fraction `f` of the
position (banking the full-size gains earned up to that point); the `(1-f)` runner
keeps riding the **unchanged** 8× disaster stop.

## Why this is distinct from the dead family

Five protect-giveback levers are dead ([[profit-ratchet-dead]]) — all **stop-based**
(deadband / ratchet / breakeven): they exit *fully* on a retrace and whipsaw out
before the trend resumes. **Scale-out is size-based, not stop-based:** it banks a
*partial* at a profit *target* and leaves a runner on the original wide stop, so it
never adds a tight stop to whipsaw against. It is the one progressive-profit-lock
variant with a non-dead prior.

**Honest prior:** trend-following theory says scale *in*, not out — banking early
caps the fat right tail (the big winners that carry these books). Scale-out almost
certainly **lowers return**; the open question is whether it improves *risk-adjusted*
(Sharpe) or trims maxDD enough to justify. Empirical, gated. Likely the 6th dead
lever — that is a successful outcome.

## Mechanism

Per trade, track the favorable-extreme open profit in ATRs (the engine and the live
chandelier already track entry / extreme / atr_entry). Reduce the held size once it
crosses `T`:

```
open_profit_atr = abs(extreme - entry) / atr_entry      # peak favorable excursion
remaining       = (1 - close_frac) if open_profit_atr >= trigger_T else 1.0
eff_pos         = pos * remaining                         # the size actually carried
```

- Monotonic: anchored to the extreme, so a pullback never un-scales (you don't
  re-add risk after banking).
- Per-trade: resets on a flip or post-stop re-entry (new entry/extreme), like the
  chandelier.
- The banked gains are already in cumulative equity (earned at full size before the
  scale point); after the scale, the runner earns/risks only `(1-f)` size — that is
  the giveback reduction.
- `close_frac <= 0` or `trigger_T <= 0` → `remaining = 1.0` (disabled, byte-identical
  to today). Default off.

### Shared pure function (research-prod parity)

```
def scaleout_remaining(open_profit_atr: float, trigger_T: float, close_frac: float) -> float:
    """Fraction of the original position still held after profit-banking. 1.0 below
    trigger_T ATRs of open profit; (1 - close_frac) at/above; 1.0 when disabled
    (close_frac <= 0 or trigger_T <= 0)."""
```

Lives in `src/mt5_trader/stops.py` (re-created — `cusum_regime` is in `features/regime.py`;
this stop/position helper module is its home). Pure, no MT5/IO.

### Engine integration

`_simulate_stops` already tracks `entry`, `extreme`, `atr_entry` per trade and runs
whenever `sl_atr_mult > 0` (the portfolio uses 8). In the engine, position size is
applied *outside* the stop sim (`pnl = leverage * (pos * ret - cost)`,
`turnover = |diff(pos)| + extra_turn`), so scale-out returns a **size-multiplier
array** the outer step folds in — it does not scale `ret` itself:
- `_simulate_stops` additionally returns `size_mult[t] = scaleout_remaining(open_profit_atr,
  cfg.scaleout_trigger, cfg.scaleout_frac)` per bar (the remaining fraction for the
  trade live at `t`).
- The outer `run` uses the effective position `eff_pos = pos * size_mult` for BOTH
  pnl (`pos * ret` → `eff_pos * ret`) and turnover (`|diff(pos)|` → `|diff(eff_pos)|`).
  Computing turnover from `eff_pos` means the scale-down step (`pos → pos*(1-f)`)
  appears automatically as an `f*|pos|` turnover (partial-close cost) — no special
  case needed. `extra_turn` (stop re-entries) is unchanged.
- `BTConfig` gains `scaleout_trigger: float = 0.0`, `scaleout_frac: float = 0.0`.
  Disabled → `size_mult` all 1.0 → `eff_pos == pos` → byte-identical to today.

## Research phase (gated)

`scripts/research_scaleout.py`: per trend book (`donchian`, `vwap_trend`, `london_orb`)
on the real feed, sweep `scaleout_trigger ∈ {1,2,3,4}` × `scaleout_frac ∈ {0.25,0.5,0.75}`
vs the no-scaleout baseline (frac=0). Report return / Sharpe / maxDD / turnover.

**Gate criterion:** keep scale-out for a book only if it **raises Sharpe OR
meaningfully cuts maxDD on a plateau, without gutting return** (a return give-up with
no risk-adjusted gain = drop). Report + sign-off.

## Deploy phase (conditional on the gate)

Runner scales each symbol's net target lots by `scaleout_remaining(open_profit_atr)`,
computing `open_profit_atr` from the existing per-symbol trail state
(`self._trail[sym] = (side, atr_entry, entry, extreme)` from `chandelier_sl`). Per-book
`scaleout_trigger` / `scaleout_frac` in `config/portfolio.yaml`, default off.

## Testing

- `scaleout_remaining` (pure): below T → 1.0; at/above T → 1-f; disabled (f<=0 or
  T<=0) → 1.0.
- Engine: scale-out disabled reproduces current results; enabled, on a constructed
  ramp-then-pullback a winner gives back LESS than baseline after the scale point,
  AND on a clean strong ramp total return is LOWER than baseline (tail-capping
  signature); the scale event adds turnover.

## Scope / non-goals

- Single scale only (one `f` at one `T`) — a ladder is a later extension only if
  single shows life. No change to entry signal, disaster stop, or netting.
- Not a tighter stop / breakeven / fade (all dead).
- Deploy conditional on the Phase-1 gate + user sign-off.

## Related

[[profit-ratchet-dead]] (stop-based protect-giveback family — scale-out is size-based,
different prior), [[strategy-robustness-levers]].
