# Spec: Breakeven stop (don't let a winner become a loser)

Status: design. Author: QR/QD. Date: 2026-06-24.

> **OUTCOME (2026-06-24): DROPPED at the Phase-1 gate.**
> Sweep (T∈{1..4} × buffer∈{0,0.1,0.2}): no cell raises/holds Sharpe on any trend
> book vs baseline — donchian Sh 1.95→1.70 (DD worse); vwap 1.84→0.37; london
> 3.90→3.35 (trims DD 0.28pp only by paying 1.1pp return). As trigger→4 it converges
> to baseline (rarely arms). Same whipsaw mechanism as the ratchet, even in this
> gentlest form. **4th dead protect-giveback lever** — the wide 8× disaster stop is
> optimal; the giveback is the price of the trend edge. Code reverted; research
> script kept. Stop pursuing this family — see [[profit-ratchet-dead]].

## Goal

Once a trade is up `T`×ATR, move its stop to ~entry so a winner cannot turn into
a loss. User intent: "make sure my giveback does not go into loss." Distinct from
the **profit-ratchet** ([[profit-ratchet-dead]]), which trailed the stop at
`extreme − R×ATR`, bit on shallow pullbacks, truncated trends, and was dropped.

## Why this is different from the dead ratchet

- Ratchet stop = `extreme − R×ATR` (follows the favorable extreme down tightly) →
  fires on shallow in-trend pullbacks → cut trends → no cell beat baseline.
- **Breakeven** floors the stop at **entry** only after `T`×ATR of profit. A
  normal shallow pullback never reaches entry, so it does not truncate trends; it
  only removes the "winner → loser" tail.

**Honest prior / failure mode:** gold pullbacks that dip to entry then resume are
common, so a breakeven stop can **whipsaw** out at entry just before the trend
continues, plus re-entry cost. Net effect is empirical — it is the *gentlest*
member of a family where 3 levers already died (deadband, volume-gate, ratchet),
so the best shot, but it must clear the same backtest gate or it is dropped.

## Mechanism

The disaster stop (chandelier `extreme ∓ 8×ATR`) is untouched until the position
is up `T`×ATR; then the stop is floored toward entry:

```
open_profit_atr = abs(extreme - entry) / atr_entry
armed           = breakeven_trigger > 0 and open_profit_atr >= breakeven_trigger
be              = entry + direction * breakeven_buffer * atr_entry   # buffer covers round-trip cost
sl              = max(disaster_sl, be) if long else min(disaster_sl, be)   # only when armed
```

- `breakeven_buffer` (in ATRs) lifts the floor a hair above entry so a stop-out is
  net non-loss after the entry+exit spread (chosen: entry + cost buffer).
- `breakeven_trigger <= 0` disables → byte-identical to today (safe default).
- For a deep winner the disaster trail is already above entry, so the floor is a
  no-op there; it only bites when price retraces toward entry on a shallow winner.

### Shared pure function (research-prod parity)

```
def breakeven_floor(sl: float, side: float, entry: float, atr_entry: float,
                    open_profit_atr: float, trigger: float, buffer: float) -> float:
    """Floor the chandelier stop at ~entry once open profit >= trigger ATRs.
    Returns sl unchanged when trigger <= 0 or not yet armed. Long: raises sl up to
    entry+buffer*atr; short: lowers sl down to entry-buffer*atr."""
```

`engine._simulate_stops` and `runner.chandelier_sl` both call it after computing
the disaster `sl` — identical backtest and live. Lives in `src/mt5_trader/stops.py`
(re-created; this is its consumer).

## Research phases (gated)

- **Phase 1 — sweep.** `scripts/research_breakeven.py`: per trend book on the real
  feed, sweep `breakeven_trigger ∈ {1,2,3,4}` × `breakeven_buffer ∈ {0, 0.1, 0.2}`
  (ATRs), vs the no-breakeven baseline (trigger=0). Report return/Sharpe/maxDD/
  turnover. **Gate:** keep a book's breakeven only if it raises or holds Sharpe on
  a plateau (and ideally trims left-tail / maxDD) vs baseline. Report + sign-off.
- **Phase 2 — deploy** (only if it clears the gate): wire `breakeven_floor` into
  `runner.chandelier_sl`; add `breakeven_trigger`/`breakeven_buffer` to
  `config/portfolio.yaml` (top-level, like `sl_atr_mult`), default off.

## Testing

- `breakeven_floor` (pure): below trigger → sl unchanged; armed long → `max(sl, entry+buffer*atr)`;
  armed short → `min(sl, entry-buffer*atr)`; `trigger<=0` → unchanged; deep winner
  where disaster_sl already past entry → unchanged (no-op).
- `engine._simulate_stops`: breakeven disabled reproduces current results; enabled,
  a winner that retraces to entry exits at ~breakeven instead of becoming a loss on
  a constructed path.
- `chandelier_sl` with breakeven: armed position's sl floored at entry+buffer.

## Scope / non-goals

- No change to the disaster stop, entry signals, sizing, or netting.
- Not a trailing ratchet, not dip-entry, not fading (all dropped/shelved).
- Per-symbol on the net position (stops are per-symbol), same as the chandelier.
- Phase 2 conditional on the Phase-1 gate + user sign-off.

## Related

[[profit-ratchet-dead]], [[strategy-robustness-levers]], [[fast-vol-defense-dead]],
[[pullback-dip-entry-dead]].
