# Gold→Silver Lead-Lag Taker — Design

**Date:** 2026-06-17
**Status:** design approved, pre-implementation

## Motivation

The portfolio has no sub-minute / microstructure book (`ofi` was never deployed). The HF research sweep found that **every** HF taker idea on gold died at scale **except the gold→silver lead-lag** — gold price moves lead silver. This is the one survivor, it uses exactly the two symbols we capture at tick resolution (XAU, XAG), fills an empty timescale, is comp-relevant (fast, high trade count), and is low-correlation to the trend/MR books.

A prior *market-making* follow-on was abandoned because there were no trade prints to backtest MM PnL. This design is the **taker** form (cross silver's spread when gold moves), which *is* backtestable on synced bid/ask ticks.

## Constraint

Build only on the real broker feeds: **`data/real` + `data/ticks`** (XAU + XAG bid/ask ticks; no L2 depth; ~1 month history + live capture). This is now the backtest default (`DEFAULT_DATA`).

## Approach — validation-gated, two phases

Mirrors the existing `sweep_fade` → strategy path: prove the edge net of silver's spread **before** building a deployable book.

### Phase 1 — Event study (the gate)

On a synced **1-second grid** of XAU + XAG (`research.load_1s` already produces 1s mid/spread per symbol):

- Define gold move `g_t = log(XAU_mid_t) − log(XAU_mid_{t−lead_secs})`.
- Measure silver's **forward return** over the next `hold_secs`, in gold's direction, **minus the XAG round-trip cost** (full silver spread + slippage).
- Output a PASS/FAIL table across `(lead_secs, z_entry, hold_secs)`.

**If it fails net of XAG spread, stop — no book is built.**

### Phase 2 — Strategy (only if Phase 1 passes)

A registered `LeadLag` strategy that slots into the existing engine the way `ratio_mr` does:

- A `leadlag_bars(xau, xag)` helper merges the two feeds into one frame whose **`close` = XAG** (engine marks P&L + cost on silver, the traded leg) plus a **gold-signal column** (gold's recent return / z-score). Strictly causal: the gold column at bar *t* uses only gold data ending ≤ *t*−1s.
- `LeadLag.signal(bars)` reads the gold column → positions **silver** (taker), held a fixed horizon. Gold is signal-only, never traded (mirror of how XAG is signal-only in `ratio_mr`).
- Runs through the same `run()` engine + `segmented_signal`, backtested on the real-spread default data.

**Data flow:** `load_1s(["data/real","data/ticks"], "XAUUSD" / "XAGUSD")` → merge on 1s ts → gold-move signal → silver position → engine (net of real XAG spread).

## Signal / entry / exit

Operate on the 1s grid (engine "bar" = 1 second; hold of `h` seconds = `h` bars).

**Signal (gold move):**
- `g_t = log(XAU_mid_t) − log(XAU_mid_{t−lead_secs})`.
- `z = g_t / rolling_std(g, z_n)` (vol-adaptive, like `vwap_trend`/`ratio_mr`).
- Fire when `|z| ≥ z_entry`; direction = `sign(g)`.

**Entry (silver, taker):** on a fire, take XAG in gold's direction (cross the spread). Re-arm on fresh spikes after the hold expires (fixed-hold state machine, like `ofi`).

**Exit:** fixed hold of `hold_secs`, then flat. Lead-lag is a brief impulse-response — silver catches up within the follow-through window, after which there is no edge. No signal-flip exit.

**Params** (swept in Phase 1, tuned in Phase 2): `lead_secs` (1–10s), `z_n` (normalization window), `z_entry` (~2), `hold_secs` (2–30s).

**Cost model — make-or-break:** the taker pays XAG half-spread on entry *and* exit ≈ one full silver spread + slippage round-trip. Silver's forward move over `hold_secs` must clear that. Phase 1 reports the edge in bp next to the XAG round-trip cost so PASS/FAIL is explicit.

**Latency assumption:** the engine acts on the *next* 1s bar after the signal — a 1-second reaction lag is baked in. Reasonable for a taker reacting to gold ticks; if the entire edge lives inside <1s, this proxy will miss it (and the edge likely isn't capturable without colocation anyway).

## Validation & testing

**Phase 1 PASS bar** (built to resist tiny-sample overfit):
- **Net edge** (silver forward-return in gold's direction over `hold_secs`, minus XAG round-trip cost) > 0 with margin.
- **Plateau, not argmax:** holds across a band of `(lead_secs, hold_secs, z_entry)`, not one lucky cell.
- **Week-by-week sign-stable.**
- **Null/placebo test:** silver return after gold-move times vs after random times → p-value (like `asian_sweep`'s p=0.005).
- **Lead-direction check:** gold→silver must beat silver→gold (confirm gold leads, not contemporaneous/reverse).
- **OOS split:** in-sample `data/real` (May), out-of-sample `data/ticks` (live) — survives both.
- Sub-minute → large n (no n≈20 problem of the daily strategies).

**Phase 2 testing:**
- **Unit tests** on pure helpers: `leadlag_bars` merge strictly causal (explicit lookahead-guard test — silver position at *t* uses only gold's move ending ≤ *t*−1s); signal fire/hold/re-arm state machine; fixed-hold exit.
- **Engine backtest** on real-spread 1s bars → net return / sharpe / hit / turnover / maxDD; cross-check it matches the Phase-1 event-study edge.

## Decision gates

- **A** — measure XAG spread on the real feed first; if it dwarfs the lead move, stop early.
- **B** — Phase-1 event study PASS/FAIL.
- **C** — Phase-2 net backtest confirms it survives as a tradeable book.

Deploy only past C, at a small weight (~0.1, like `asian_sweep`), and forward-confirm before sizing up.

## Out of scope

- L2-depth / order-flow features (no depth captured).
- The market-making variant (not backtestable without trade prints).
- New symbols beyond XAU/XAG.
- Multi-day / event-driven books (separate specs; multi-day is un-validatable on ~1 month).
