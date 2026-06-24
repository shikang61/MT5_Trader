# Spec: give_back daily trailing kill (net-level)

Status: design. Author: QR/QD. Date: 2026-06-24.

## Goal

Find and enable a daily **trailing kill** fraction `g` that protects an intraday
gain at the **portfolio (net) level**: if net equity retraces `g` from the day's
peak, flatten everything and stand down flat for the rest of the UTC day
(auto-resume next day). This is "lock in profit" at the level that can actually
work — the netted book — unlike the per-trade levers (all dead) and scale-out
(washed out in the net).

## Already built — this is validate-then-enable

The mechanism exists and is unit-tested; this task adds **no live code**:
- `RiskManager.give_back` (config `risk.yaml posture.give_back`, default **0.0 = off**).
- `RiskManager.kill`: trips when `equity/day_peak - 1 <= -give_back` (or the
  separate `loss_limit` 4% from day-start). Sticky for the UTC day; `roll_day`
  resets the anchor + tracks the peak next day.
- Tests: `tests/test_risk.py::test_trailing_kill_protects_peak`,
  `test_give_back_off_by_default`.

The only open question is **what `g` (if any) helps the net book** — a backtest,
then a one-line config change.

## Honest prior

Daily trailing kills commonly **whipsaw**: flatten on a midday dip, then miss the
afternoon recovery → realized loss + forgone gain. The in-sample net is already
Sharpe-9 / maxDD −0.95%, so there may be little tail to protect. Skeptical — likely
shelved at the gate, like the rest of the protect-giveback family. The win we are
looking for is **tail/drawdown insurance** (fewer/shallower bad days), accepting a
small return give-up — not a Sharpe boost on this benign window.

## Mechanism (mirrors `RiskManager.kill` exactly)

Per UTC day on the net equity curve: anchor `day_start` and `day_peak` at the first
bar; update `day_peak = max(...)` each bar. On any bar where
`equity/day_peak - 1 <= -g` (or `equity/day_start - 1 <= -loss_limit`), set the
position to 0 for the **rest of that UTC day** (sticky); reset at the next UTC day.

## Research phase (gated)

`scripts/research_giveback_net.py`: reuse the net-frac construction + bar sim from
`research_scaleout_net.py` (all books summed → net position, chandelier 8×ATR
disaster stop, real-spread cost). Add the daily-kill overlay above (both
`loss_limit=0.04` and the swept `give_back`). Sweep
`give_back ∈ {0, 0.01, 0.015, 0.02, 0.03, 0.04}` (0 = baseline: loss_limit only,
no trailing kill). Report per `g`: return%, Sharpe, maxDD%, turnover, and
**#kill-days** (how often it fires).

**Gate:** keep a `g` only if it **cuts net maxDD / the left tail materially while
holding return and Sharpe**, on a plateau (neighbouring `g` agree). A pure return
give-up with no tail protection → drop. Report + sign-off.

## Deploy phase (conditional)

Set `posture.give_back: <g>` in `config/risk.yaml` (config only — `RiskManager`
already implements + tests it). Default stays off if nothing clears the gate.

## Testing

- Live mechanism: already covered by `tests/test_risk.py` (no new unit test needed).
- Research harness is exploratory (run-and-report), matching the other
  `scripts/research_*.py`; the daily-kill overlay must reproduce `RiskManager.kill`'s
  rule (verify by spot-checking a known kill day in the output).

## Scope / non-goals

- No new live code; net-level only; single `g` (no intraday/per-book variants).
- Not a per-trade stop, not scale-out (both dead/shelved).
- Deploy conditional on the gate + sign-off.

## Related

[[profit-ratchet-dead]] (protect-giveback family — this is the daily/net-level
variant, the right level), [[strategy-robustness-levers]],
`2026-06-17-standing-risk-posture` (the kill-switch design).
