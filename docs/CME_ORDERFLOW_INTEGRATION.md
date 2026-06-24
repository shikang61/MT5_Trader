# CME Order-Flow Integration — Roadmap

A concrete, sequenced path to bring **real exchange order-flow** (CME COMEX gold
`GC` / silver `SI`) into research and live, and what it unlocks. Complements
[`DATA_SOURCES.md`](DATA_SOURCES.md) (the vendor/option landscape) — this doc is the
*how/when/what-it-touches*, not a re-listing of vendors.

## Why this, why now

Five price-only reversal/giveback levers have been tested and dropped (deadband,
profit-ratchet, breakeven, fast-vol de-size, CUSUM regime gate — see the
`profit-ratchet-dead` memory). The consistent verdict: **you cannot reliably tell a
pullback from a reversal in price alone** at our timeframes. The one frontier not
yet falsified is the **order book** — real aggressor flow, absorption, and
queue dynamics at the turn. Spot gold (`XAUUSD` CFD) is **L1-only** (OTC, no real
book), so that signal is physically absent from the current live feed. It exists on
the **CME futures book** (`GC`/`SI`). That is the gap this roadmap closes.

## What already exists (the pipeline is ~90% there)

The depth chain is built and tested; only the live **source** is missing:

| layer | file | status |
|---|---|---|
| Canonical depth schema (`bid_px_1..5`, `bid_sz_1..5`, …) | `src/mt5_trader/data/schema.py` (`depth_columns`) | ✅ exists |
| Vendor → parquet ingest | `src/mt5_trader/data/ingest.py` + `config/schema_map.yaml` + `scripts/ingest_dump.py` | ✅ exists (ingested the comp L2 dump) |
| Microstructure features (`ofi`, `l1_imbalance`, `depth_imbalance`) | `src/mt5_trader/features/microstructure.py` (`with_micro_features`) | ✅ exists |
| Consumer strategy | `src/mt5_trader/strategies/orderflow_imbalance.py` (`OFIMomentum`, registered `ofi`) | ✅ exists (benched: needs real depth) |
| Live tick/depth logger | `scripts/log_ticks.py` (L1 today) | ⚠️ L1 only — extend for depth |
| Backtest engine + research harness | `backtest/engine.py`, `pipeline/data.build_bars` | ✅ reuse as-is |

So integration is **data-sourcing + a few wiring points**, not new architecture.

## Phased plan (cheap → expensive, each gated)

### Phase 0 — free probe: does the MT5 broker expose any DOM? (hours)
Extend `scripts/log_ticks.py` with `mt5.market_book_add("XAUUSD")` then
`mt5.market_book_get(...)`. Empty ⇒ the broker gives no ladder (likely, it's a CFD).
- If non-empty: free L2 into the existing schema — **but CFD-aggregated, not a true
  book** (same caveat that benched `ofi`). Low-value; treat as a stopgap only.
- **Decision:** don't over-invest here; it mostly confirms the need for CME.

### Phase 1 — historical truth: Databento `GC`+`SI` MBP-10 (1–2 days, trial $)
Pull CME **MBP-10** (or MBO) history for `GC` and `SI` via Databento's Python API.
- Map vendor columns → canonical ladder in `config/schema_map.yaml`; ingest with
  `scripts/ingest_dump.py` → `data/processed/<SYMBOL>/*.parquet` (reuses the exact
  path the comp dump used).
- Contract notes: trade the **front month**, stitch a continuous series (roll on
  volume/OI, back-adjust); `GC`/`SI` are the liquid books (`MGC`/`SIL` micros are
  thinner — use full-size for signal research). Keep `XAUUSD`/`XAGUSD` spot for
  execution mapping later.
- Output: a real CME book in the same schema `with_micro_features` already reads.

### Phase 2 — re-test on the REAL book (GATE) (research, no spend beyond Phase 1)
Re-run, on the genuine queue, the studies that died on the CFD aggregate:
- **OFI momentum** (`OFIMomentum`) — real per-order flow, not LP aggregation.
- **Gold→silver lead-lag** (the one HF edge that survived) — now with real depth.
- **NEW — order-flow reversal detector**: absorption / delta-divergence at the
  favorable extreme (large opposing size absorbing without price progress = the
  exhaustion signal price-only CUSUM couldn't find). This is the direct successor to
  the dead reversal work.
- **Gate criterion** (same culture as every lever this session): a candidate must
  show a param **plateau**, survive realistic **cost**, and pass a **placebo/OOS**
  check — not a single hot cell. Reality check: CFD HF was dead, but a true book is a
  different instrument, so a *fresh* test is justified; do **not** re-buy depth just
  to re-run the dead CFD studies.
- **STOP for sign-off** on the numbers before any live spend.

### Phase 3 — live (only if Phase 2 pays): IBKR depth logger (days)
We already have IBKR API access (cheapest route to live CME depth).
- New logger (mirror `log_ticks.py`) using IBKR `reqMktDepth` on `GC`/`SI` → write
  the **same parquet schema** on the VPS. No engine/strategy changes — they already
  read the schema.
- Add the winning order-flow book(s) to `config/portfolio.yaml` (default off →
  paper → ramp), executed on the spot symbol the broker fills, signal from the
  futures book (a documented spot-vs-futures basis mapping).
- Databento live is the alternative feed if IBKR depth proves too shallow/laggy.

## What it unlocks (payoff)
- A **reversal detector that L1 cannot build** (real absorption/OFI at the turn) —
  the only un-falsified reversal avenue left.
- Revives `OFIMomentum` and the gold→silver lead-lag on a genuine book.
- Richer regime/vol features (queue imbalance) for the existing gates.

## Cost / effort summary
| phase | spend | effort | gate |
|---|---|---|---|
| 0 broker DOM probe | free | hours | confirms CME need |
| 1 Databento history | trial → ~$/GB | 1–2 days | data lands in schema |
| 2 re-test on real book | (Phase-1 data) | research | **plateau + cost + placebo/OOS** |
| 3 live IBKR depth | ~$10–110/mo CME sub | days | only if Phase 2 clears |

**Suggested first step:** Databento trial → pull `GC`+`SI` MBP-10 → Phase-2 OFI /
reversal re-test. Skip Phase 0 unless a free broker DOM is trivially available.

## Related
`DATA_SOURCES.md` (vendor landscape), `data-sources-scaling` + `hf-strategy-research`
+ `profit-ratchet-dead` memories, `features/microstructure.py`,
`strategies/orderflow_imbalance.py`.
