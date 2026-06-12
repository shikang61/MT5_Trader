# Lessons Learnt

Running log of what this project taught us — research, engineering, process.
Started 2026-06-12 (prep phase, three days before the competition data drop).

## Research

1. **Costs decide everything.** Every strategy that died, died of transaction
   costs, not of a wrong idea. Turnover × (spread + slippage) is the first
   number to compute, not the last. The fade leg's "edge" was real (~0.5bp)
   and still 10× too small to pay the spread.

2. **Timeframe is a first-class parameter.** The single biggest improvement
   of the prep phase was moving from 1m to 5m bars: donchian went from −7.4%
   (benched) to +5.1% (Sharpe 4.2) with *identical logic*. 1-minute bars are
   microstructure noise plus churn. Sweep timeframes before declaring a
   strategy dead — and before declaring it alive.

3. **Hot cells are not edges.** A +17bp result that sits alone in a parameter
   grid whose neighbors are +3bp is selection bias, not alpha (BTC taker-flow).
   Real edges form plateaus: donchian@5m stayed +4% to +8% across every
   parameter wiggle. Judge regions, not cells.

4. **Failed studies pay rent when inverted.** vwap_trend — the best
   single-strategy backtest (+5.7%/mo) — came from a VWAP *reversion* study
   that failed at −4bp. A signal that loses consistently is a signal; flip it
   and re-validate honestly (dedupe, weekly splits) before believing it.

5. **Edges are asset-specific.** Both validated gold strategies lost money on
   EURUSD, GBPUSD, and BTC. That's reassuring, not disappointing: a strategy
   profitable on everything is usually curve-fit; one that matches a story
   about the instrument (gold trends, FX mean-reverts) is capturing something.

6. **Resolution can hide or fabricate signals.** The regime detector saw zero
   trends in a month because minute-level noise inflated the efficiency-ratio
   denominator (max ER 0.28 vs a 0.35 trigger); coarsening to 15m fixed it.
   Conversely, weekend-stale quotes fabricated an FX-triangle "arbitrage" —
   all 433 beyond-cost seconds sat in two Sunday-open minutes. Check the
   timestamps of your best events before believing them.

7. **Taker strategies are dead at the seconds level; maker is the only door.**
   Sub-minute autocorrelation, lead-lag, spike continuation: every gross edge
   measured was ±0.2bp against ~3bp taker costs. The microstructure does
   mean-revert — only passive (limit-order) execution can harvest it, which
   is a fill-model research question for the competition's depth data.

8. **One month, one instrument is thin evidence.** Weekly splits, parameter
   wiggles, and cost stress are the minimum; even then, vwap_trend's P&L
   concentrates in trending weeks. Re-validate everything on the competition
   dump — different feed, different period.

## Engineering

9. **The test suite catches real money bugs.** The registry test caught
   vwap_trend crashing on live bars (n_ticks vs tick_volume) before it ever
   ran on the VPS. The no-lookahead engine test is the reason backtest
   numbers are believable at all. Tests gate every deploy: cheap insurance.

10. **Reconcile-to-target beats order-by-order.** Idempotent restarts, safe
    manual intervention, free position cleanup. But know your account mode:
    on hedging accounts a bare counter-order *opens* an opposite position —
    close by ticket first.

11. **Broker-side protection survives dead software.** A 3×ATR stop attached
    to the position outlives the runner, the Python process, and the VPS.
    Wide disaster stops + strategy-logic exits beat tight stops that fight
    the strategy. Ratchet them (never loosen) — recomputing from current
    price each bar quietly walks the stop away from danger.

12. **Time is never what you think.** MT5 server time is UTC+2/+3, not UTC;
    Dukascopy months are 0-indexed; vol annualization assumed 1m bars and
    silently under-sized M5 books by √5. Every time-like quantity needs an
    explicit unit and zone check at the boundary.

13. **`.gitignore` patterns match everywhere.** `data/` swallowed
    `src/mt5_trader/data/` — the package worked locally (untracked files)
    and broke on clone. Anchor ignore patterns to root (`/data/`).

14. **Headless Windows has sharp edges.** Git Credential Manager needs an
    interactive desktop (use a read-only deploy key); SSH-started processes
    die with the session (use a scheduled task); GUI apps like the MT5
    terminal survive RDP *disconnect* but not *sign-out*; pause Windows
    Update or it reboots mid-round.

15. **Log decisions, not just trades.** The JSONL audit log (signal, vol,
    targets, fills, prices per step) made every diagnosis in this project
    possible — including spotting the vol-annualization bug from two numbers
    that should have matched and didn't. Per-book targets in the log are
    what make per-strategy P&L attribution possible at all.

## Process

16. **Evidence gates beat conviction.** The book changed three times in one
    day (ratio_mr out, donchian out then back in at 5m, vwap_trend in)
    purely on backtest evidence, with the criteria written down *before*
    running. The strategies that feel sophisticated (OFI, regime fades,
    flow-following) all lost to two dumb structural effects executed cleanly.

17. **One-way code flow keeps live trading sane.** Mac → GitHub → VPS, the
    VPS only ever pulls, risk posture is YAML, params freeze before rounds.
    Every deploy is 30 seconds and reviewable; nothing is hand-edited on the
    box that holds positions.

18. **Verify the runner is *live*, not just running.** "No errors" and "no
    output" look identical. Heartbeat events distinguish a quiet market from
    a dead process; the watchdog turns that into a notification instead of a
    morning surprise.
