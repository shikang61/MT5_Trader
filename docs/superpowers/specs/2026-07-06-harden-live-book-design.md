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

---

## OUTCOMES (2026-07-07)

All workstreams ran on BOTH feeds; verdicts issued on the real broker feed
(data/real + data/ticks, ~0.27bp spread, live ticks synced through Jul 6 23:00
UTC) with Dukascopy (~1.46bp) as the cost-conservative check. Full agent
reports in the session; summary:

| Item | Verdict | Evidence |
|---|---|---|
| A1 deadband | **DROPPED at design time** | Already researched & rejected Jun 24; bleed was signal losses, not churn (Jun 25–Jul 6 attribution gap only +$80) |
| A2 sweep_fade timeframe | **KEEP M1** | M5 fails gates 1+4 both feeds; M15 dead. M1 survives 4× on real feed (IS +0.75%) |
| A2 asian_sweep timeframe | **KEEP M1** | M5 fails gates 1,3,4. asian_sweep@M1 = only 4×-cost survivor full-span in the whole matrix (+1.27% real). Donchian's M1→M5 lesson does NOT transfer: snapback books pay a one-bar delay at coarser bars |
| A3 net-marginal | **NO weight changes** | Real feed OOS: donchian POSITIVE unambiguous (remove → Sh 5.75 → −2.88); all minor books FLAT at 1×. Duka's vwap_trend-NEGATIVE and ratio_mr-POSITIVE were cost artifacts — both flip to FLAT on real spread. No london_orb/lbma_fix revival. Jul 6 config (9b21f20) validated |
| B regime gate | **DO NOT ADOPT** | ER forced-flat inverts the book (+12.3% → −3.6% duka; +8.3% → −5.0% real). CUSUM OOS-negative in 9/9 wiggle cells. ER entry-only closest: helps duka OOS/chop with plateau, but never beats ungated full-span on the real feed — re-test after a chop-heavy month of live ticks |
| B exit_n | **KEEP 110 — OOS-vindicated** | Beats 60 everywhere (real full-span +8.3% vs +3.4%); 90–130 a true plateau; 90's duka edge flips sign on real feed (noise). Donchian's chop loss is regime-normal, not a parameter defect |
| C passive geometry | **KEPT 37.5/25 (user decision)** | Research said revert (fails gates 1,3,4; ranking statistically a coin flip, |t|≤1.35; surface is noise; no cell shows robust edge — live +$114/52 trades ≈ 0.35σ). User kept it; review condition: judge on realized broker PnL after ~30 trades at 37.5/25 (~2–3 wks), revisit if it lags the 20/25 live track record |

Shipped artifact: `scripts/eval_net_marginal.py` — the standing gate for any
future weight change (replaces reactive daily hand-reweighting).

Net conclusion: the live config needs no further changes. The portfolio is one
real edge (donchian) plus cheap, roughly-neutral diversifiers; the Jun 25–Jul 6
loss was dominated by two books (london_orb, lbma_fix) that are now off, plus
regime-normal trend-book chop. Standing re-test triggers: entry-only ER gate
(after a chop-heavy month), passive geometry (at ~30 trades), sweep_fade
(if 4×-stress marginal goes negative on the real feed).
