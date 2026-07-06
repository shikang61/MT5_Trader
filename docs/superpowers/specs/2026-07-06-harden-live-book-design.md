# Harden the live book — research design

**Date:** 2026-07-06
**Trigger:** Broker attribution (Jun 25 – Jul 6): portfolio (magic 1001) realized
**−$1,184** (165 trades, 40% win) while the passive book (magic 2003) made +$115.
Full-span (Jun 16 – Jul 6) modeled attribution: gross **+$866** vs cost/slippage
gap **−$1,130** — the books' combined gross edge is smaller than the churn bill.
Donchian is the only positive-gross book (+$1,926); every other book is negative.
london_orb and lbma_fix were zeroed same day (commit 9b21f20).

**Goal:** make the surviving live book (donchian 0.55, sweep_fade 0.15,
vwap_trend 0.10, ratio_mr 0.10, asian_sweep 0.10 + passive consolidation)
robustly net-positive, using **current data only** (no multi-month backfill).

**Data & windows:**
- In-sample: May 12 – Jun 11 Dukascopy tick dump (`data/processed/`, bars cache).
- **True OOS: Jun 16 – Jul 6 live period** — real feed, real fills
  (`logs/portfolio.jsonl` pulled from the VPS, broker deal history by magic).
  This window did not exist when any current parameter was chosen.

**Acceptance gates (stricter, applied to every candidate change):**
1. Improves **net-of-cost** return/Sharpe on the full span.
2. Improves (or does not hurt) the Jun 16 – Jul 6 live OOS window.
3. Parameter **plateau**, not a hot cell (LESSONS.md #3).
4. Survives **4× cost stress**.
No live config change ships unless all four pass; adopted changes carry inline
evidence comments in `portfolio.yaml` (existing convention).

## Workstream A — cost-first surgery

**A1. Rebalance deadband sweep — RESOLVED AT DESIGN TIME, dropped.** The Jun 24
deadband plan already built and evaluated exactly this and dropped it at its
decision gate: discrete {−1,0,1} book signals make a signal deadband a no-op,
measured real spread cost is ~$31/week, and the large modeled-gross-vs-equity
gap is an **attribution artifact, not cost** (see plan
`2026-06-24-deadband-and-error-context.md`, OUTCOME note). Consistent with the
Jun 25 – Jul 6 window, where the gap was only +$80 — the bleed was signal
losses, not churn. **Consequence: Workstream B is the highest-value item.**

**A2. Timeframe sweep for surviving M1 books.** Run `sweep_fade` and
`asian_sweep` at M1 vs M5 (identical logic) on the dump + OOS window — the
change that took donchian from −7.4% to +5.1% (LESSONS.md #2) was never tried
on these two. **Deliverable:** per-book M1/M5 net comparison + verdict.

**A3. Systematized net-marginal weight test.** Script the ad-hoc "does w=0
lift net portfolio Sharpe?" test (used to kill london_orb) so it runs for
every book over full span + OOS in one command. This replaces reactive daily
hand-reweighting as the standing gate for weight changes. **Deliverable:**
`scripts/eval_net_marginal.py` + current-book report.

## Workstream B — fix the anchor (Donchian)

Donchian lost −$200 in the Jun 25 – Jul 6 chop. Two questions, separately:
1. **Regime gate:** does gating donchian on the existing 15m-coarsened
   efficiency-ratio / CUSUM regime machinery (`features/regime.py`,
   `eval_regime_gate.py`, `eval_donchian_regime_stop.py`) keep it out of
   range-bound tape without giving back trend capture? Walk-forward May → Jul.
2. **exit_n=110 re-test:** adopted Jun 24 despite failing deflated-Sharpe; the
   live window is its first OOS evidence. Re-run the exit_n grid including the
   live period; check 110 still sits on the 90–130 plateau.
**Deliverable:** gate on/off × exit_n matrix, net of costs, both windows.
Regime gates flatter in-sample — Workstream B carries the strictest reading of
gates 2–3.

## Workstream C — validate the maker book's new geometry

Commit 1ea7b41 (today) changed passive TP/SL to 37.5/25 bp (1.5:1) with no
backtest recorded. Replay tick data through the passive fill model
(`eval_passive_limits.py` / `research/` tooling) at old (20/25) vs new
(37.5/25) geometry plus a small neighborhood grid. Also note (not implement)
the equity-scaled-sizing issue from CASH_DEPLOYMENT.md §6. **Deliverable:**
geometry comparison; revert recommendation if the new setting is worse.

## Non-goals

New instruments, new signal families, multi-month backfill, cash-account
transition work. Workstreams are independent; any can ship alone.
