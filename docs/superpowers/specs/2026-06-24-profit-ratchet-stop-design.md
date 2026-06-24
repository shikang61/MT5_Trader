# Spec: Profit-ratchet stop + tick-volume reversal gate

Status: design. Author: QR/QD. Date: 2026-06-24.

> **OUTCOME (2026-06-24): DROPPED at the research gates.**
> Phase 0 diagnostic: giveback is material (donchian median 4.6 ATR), but tick
> volume does NOT separate reversal from resumption (n_ticks z separation ≈ +0.01
> donchian / −0.09 vwap / +0.13 london — noise) → volume gate killed before build.
> Phase 1 sweep: the price-only ratchet beats baseline on NO (T,R) cell for any
> trend book (donchian Sh 1.95→best −0.40; vwap 1.84→1.24; london 3.90→1.15),
> turnover explodes 2–5×. Conclusion: the pullback giveback is the *price of the
> trend edge*, not a fixable leak — protecting it cuts expectancy. All ratchet
> code reverted; research scripts kept. Don't re-test profit-protection on these
> trend books without a materially different sample/mechanism.

## Goal & hypothesis

Trend books give back open profit on in-trend pullbacks (observed live: 2026-06-23
LondonBreakout shorted at 07:41, was ~+$24 at the 08:08 low, then gold pulled back
up ~$25 into the stop region — gave the gain back — before the down-trend resumed).
We want to **protect open profit on deep winners without cutting trends early**.

**Hypothesis:** a *late-arming second stop* that engages only after a position is
already deeply in profit (≥ `T`×ATR) can bank a fraction of the giveback while
leaving normal entries on the wide disaster stop. Optionally, a **tick-volume
spike at the favorable extreme** flags a likely reversal (vs a quiet pullback) and
makes the protection more aggressive.

This is FILTERED profit-protection, deliberately distinct from:
- **dip-entry / buy-the-dip** — shelved ([[pullback-dip-entry-dead]]); we are NOT
  adding to positions.
- **reopen-fade** — shelved because fading cancels trends ([[strategy-robustness-levers]]);
  we are NOT fading.
- **uniformly tighter disaster stop** — the `sl_atr_mult` sweep was monotone
  (tighter = worse return AND DD down to the 8× sweet spot). The ratchet
  **never touches the entry-side stop** — it only acts after `T`×ATR of profit, so
  it sidesteps that negative prior.

## Strong priors to respect (why this is research-gated)

1. Tighter trailing stops bled the trend books (sl sweep). Mitigation: ratchet
   arms late, only on already-profitable trades.
2. Gold volume signals run **backwards** — "volume-confirms-breakout is BACKWARDS
   on gold (high-vol breaks fade)" ([[velocity-breakout-gate]]). A volume spike at
   the *extreme* reading as exhaustion/reversal is consistent with that, but gold
   volume is finicky → the volume gate is proven on top of the price-only ratchet,
   never assumed.
3. Spot gold has only **tick volume** (`n_ticks` per bar from `build_bars`), a
   proxy for activity, not true traded volume (that is CME futures, not in the
   feed — [[data-sources-scaling]]).

Like the deadband ([[../specs/2026-06-24-deadband-and-error-context-design]]), this
may be **dropped at a gate** — that is a successful outcome, not a failure.

## Mechanism

The disaster stop stays exactly as-is. The ratchet is a **mult swap** inside the
existing chandelier (`runner.chandelier_sl`, mirrored in `engine._simulate_stops`):

```
open_profit_atr = abs(extreme - entry) / atr_entry      # favorable excursion, in ATRs
armed           = open_profit_atr >= trigger_T          # sticky: extreme only improves
mult            = ratchet_R if armed else sl_mult       # ratchet_R < sl_mult (e.g. 2-3 vs 8)
sl              = extreme - direction * mult * atr_entry # same chandelier formula
```

- `armed` needs **no extra state** — `|extreme - entry|` is monotonic once the
  position is held (extreme only moves favorably), so once armed it stays armed.
- Pre-arm behavior is byte-identical to today (mult = `sl_mult`). Only deep
  winners (≥ `T`×ATR open profit) get the tighter `ratchet_R` trail.
- `ratchet_R <= 0` or `trigger_T <= 0` disables the ratchet → pure current
  behavior (safe default).

### Tick-volume reversal gate (Phase 2 add-on)

Once armed, modulate `ratchet_R` by a tick-volume spike at the current bar:

```
spike = (n_ticks - mean_n) / std_n  >  V        # z-score over a lookback window
mult  = ratchet_R_tight if (armed and spike) else (ratchet_R if armed else sl_mult)
```

Spike → reversal-likely → tighter trail (`ratchet_R_tight`). Quiet pullback →
the loose trail `ratchet_R`, keep riding. The spike z-score is a pure helper on
`n_ticks`. (`ratchet_R` is the loose/base ratchet; `ratchet_R_tight` is the
spike-only tightening, with `ratchet_R_tight < ratchet_R`.)

### Shared pure function (research-prod parity)

```
def ratchet_mult(open_profit_atr: float, sl_mult: float,
                 trigger_T: float, ratchet_R: float,
                 ratchet_R_tight: float = 0.0, volume_spike: bool = False) -> float:
    """Effective chandelier mult. Returns sl_mult until open profit >= trigger_T
    ATRs (ratchet disabled when ratchet_R <= 0 or trigger_T <= 0); then ratchet_R,
    or ratchet_R_tight when volume_spike (and ratchet_R_tight > 0)."""
```

`runner.chandelier_sl` and `engine._simulate_stops` both compute
`open_profit_atr` from their own (extreme, entry, atr_entry) and call this one fn —
so the backtest and live trail are identical.

## Research phases (each gated; report numbers, get sign-off)

- **Phase 0 — diagnostic (cheap, decisive).** `scripts/research_giveback.py`: on the
  trend books over the real feed, measure (a) the giveback distribution (peak
  open-profit-in-ATRs → realized), and (b) the KEY test of the volume idea — at
  favorable extremes, is `n_ticks` (z-score) elevated before a **reversal** vs a
  **resumption**? Classify each local extreme as reversal/resumption by the
  forward path. If `n_ticks` does not separate the two, Phase 2 is dead before we
  build it. No code change to the engine/runner in this phase.
- **Phase 1 — price-only ratchet.** Add `ratchet_mult` + wire into
  `engine._simulate_stops`; `scripts/research_ratchet.py` sweeps (`T`, `R`) per
  book. **Gate:** keep a book's ratchet only if it raises (or holds) net Sharpe on
  a plateau vs the no-ratchet baseline.
- **Phase 2 — volume gate.** Add the `n_ticks`-spike modulation; sweep
  (`V`, `ratchet_R_tight`); **gate:** must beat the Phase-1 price-only ratchet.
- **Phase 3 — deploy.** Wire `ratchet_mult` into `runner.chandelier_sl`; add
  per-book/per-symbol params (`ratchet_trigger`, `ratchet_mult`, and Phase-2
  volume params) to `config/portfolio.yaml`, default off. Deploy only what passed.

## Testing

- `ratchet_mult` (pure): below trigger → `sl_mult`; at/above trigger → `ratchet_R`;
  `ratchet_R<=0`/`trigger_T<=0` → `sl_mult` (disabled); `volume_spike` →
  `ratchet_R_tight` when set, else `ratchet_R`.
- `chandelier_sl` with ratchet: a deep-in-profit position gets a tighter sl than
  the 8× disaster stop; a shallow position is unchanged vs today.
- `engine._simulate_stops` parity: ratchet disabled reproduces current results;
  enabled, a deep winner exits earlier (less giveback) on a constructed path.
- Volume spike z-score helper: spike flagged above `V`, not below.

## Scope / non-goals

- No change to the disaster stop, entry signals, sizing, or netting.
- Not dip-entry, not fading (both shelved).
- Phases 1–3 are conditional on the preceding gate + user sign-off.
- Per-symbol on the net position (stops are per-symbol, not per-book) — same as the
  existing chandelier.

## Related

[[strategy-robustness-levers]], [[pullback-dip-entry-dead]],
[[velocity-breakout-gate]], [[fast-vol-defense-dead]], [[data-sources-scaling]],
[[eod-asian-session-windows]].
