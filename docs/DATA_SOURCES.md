# Market Data Sources — Scaling Historical & Order-Book Depth

Reference for growing the dataset beyond the current `data/real` (comp depth dump,
May 11–Jun 10) + `data/ticks` (live MT5 logger, Jun 16→, **L1 only**). Captures the
options for more history and for real order-book depth.

## Key structural insight: spot CFD vs exchange futures

**Spot gold/silver (`XAUUSD`/`XAGUSD` CFD) has no real order book.** It is OTC — an MT5
broker shows only its own liquidity-provider aggregation (best bid/ask + size = **L1**),
which is why `data/ticks` carries no ladder. **A genuine central limit order book exists
only on the exchange: CME COMEX futures** — gold `GC` (micro `MGC`), silver `SI` (`SIL`).
Every "get real depth" answer routes through CME futures (or LMAX for spot).

## 1. More historical data

| source | covers | depth | cost | note |
|---|---|---|---|---|
| **Dukascopy** | FX + metals (XAU/XAG), ~years, tick | L1 | free | already wired (`scripts/fetch_dukascopy.py` → `data/processed`); spread ~1.37bp (≈6× the real feed) |
| **HistData** | FX + metals, 1m + tick to ~2000 | L1 | free | clean, deep history |
| **MT5 broker** (`copy_ticks_range`) | your broker, months–few yrs | L1 | free | your real spread; short, broker-capped lookback |
| **Databento** | CME `GC`/`SI`/`MGC`/`SIL` | **L2/L3 (MBO + MBP-10)** | pay/GB | ⭐ true historical book, modern Python API |
| **CME DataMine** | CME official | MBO/MBP | $$$ | bulky, institutional |
| **Refinitiv Tick History**, Tardis.dev (crypto), Kaiko (crypto) | broad | full depth | $$$ | institutional |

Spot history → Dukascopy / HistData (free, L1). Depth history → **Databento** (CME futures).

## 2. Real-time order-book depth — platforms / APIs

| route | depth | how | fit |
|---|---|---|---|
| **Interactive Brokers (IBKR)** | L2 DOM ~10 levels on futures | `reqMktDepth` (TWS / Web API) | ⭐ one account: futures + depth + API; CME depth sub ~$10–110/mo. We already have IBKR API access. |
| **Databento (live)** | CME **MBO** (every order) / MBP-10 | pure data API, no broker | ⭐ best raw depth feed |
| **Rithmic / CQG / dxFeed** | full DOM, low-latency | FIX / API | pro order-flow standard |
| **LMAX Exchange** | genuine CLOB for **spot** FX/metals | FIX / API | the only spot venue with a real book (institutional minimums) |
| **MT5 broker DOM** | 5–10 levels, shallow/synthetic | `mt5.market_book_get` | cheapest, but CFD aggregation, not a true book |

For metals depth: **CME futures via IBKR (already have) or Databento.**

## 3. Getting depth into this pipeline — we're ~90% there

The pipeline **already ingests L2**: the comp dump carried `bidprices[]/bidsizes[]/
askprices[]/asksizes[]`, `src/mt5_trader/data/schema.py` defines `depth_columns`, and
`features/microstructure.with_micro_features` derives `l1_imbalance`, `depth_imbalance`,
`ofi`. The gap is purely the live **source**. Lowest-friction first:

1. **Extend `scripts/log_ticks.py` with `mt5.market_book_add` / `market_book_get`** — IF
   the broker exposes DOM. Same parquet schema, same VPS infra, free. Test:
   `mt5.market_book_add("XAUUSD")` then `market_book_get(...)` — empty ⇒ broker gives no DOM.
2. **Databento → parquet** in the canonical ladder schema — pull `GC`/`SI` MBP-10 history,
   develop, then live-subscribe. Cleanest for true depth.
3. **IBKR `reqMktDepth` logger** — log futures L2 to the same schema.

## Reality check before spending

Prior research found gold **microstructure / HF mostly DEAD at scale** — only gold→silver
lead-lag survived; `ofi` was benched for needing real depth (`orderflow_imbalance`). BUT
that was on the **CFD broker-aggregated** book. A **true CME futures book (`GC`/`SI`)** is a
different animal (genuine queue, real OFI), so a *fresh* test there is justified — do not
re-buy depth to re-run the dead CFD studies.

**Suggested path:** Databento trial → pull `GC`+`SI` MBP-10 history → re-test OFI / lead-lag
on the real book → if it pays, log live via IBKR (already have) into the existing schema.
