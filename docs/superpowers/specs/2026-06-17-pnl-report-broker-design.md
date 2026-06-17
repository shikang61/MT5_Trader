# pnl_report — broker realized P&L by magic (cover the passive book) — Design

**Date:** 2026-06-17
**Status:** Design approved; pending implementation plan
**Type:** Feature (reporting) + small refactor (DRY)

## Motivation

`scripts/pnl_report.py` attributes per-strategy gross P&L for the **portfolio bot**
(magic 1001) by reconstructing each book's mark-to-market from the `portfolio.jsonl`
STEP events. It does **not** cover the **consolidation passive book** (magic 2003,
logged separately in `passive_consolidation.jsonl`).

The passive log can't yield P&L: its `equity` field is *shared account* equity and it
records no fill/exit price or per-trade profit. The accurate source is **broker
realized deals grouped by magic** — which `dashboard/remote_probe.py` already computes
(`history_deals_get` → `{magic: {net, gross, swap, n, wins}}`). This work factors that
out and surfaces it in `pnl_report`.

## Approach (chosen: A)

Extract `remote_probe`'s deals-by-magic aggregation into a shared pure function; add a
`--broker` mode to `pnl_report` that SSHes the VPS, runs the aggregation against live
MT5, and prints a labeled per-magic table. Default behaviour (offline per-strategy
attribution) is unchanged.

Rejected: B (separate VPS script — not "in pnl_report" as asked); C (read a pulled
remote_probe JSON — extra manual produce/pull step).

## Components

### 1. `src/mt5_trader/live/broker_pnl.py` (new)

Pure, testable aggregation extracted from `remote_probe`:

```
MAGIC_LABELS = {
    1001: "portfolio (all books)",
    2002: "pdhl passive (retired)",
    2003: "consolidation passive",
}

def realized_by_magic(deals) -> dict[int, dict]:
    """Aggregate CLOSED broker deals by magic. Only DEAL_ENTRY_OUT deals carry
    realized P&L. Returns {magic: {"net", "gross", "swap", "n", "wins"}} where
    net = profit + commission + swap, gross = profit. `deals` is any iterable of
    objects with .magic/.entry/.profit/.commission/.swap (MT5 deal objects)."""
```

`DEAL_ENTRY_OUT` is passed in / imported so the function stays pure and unit-testable
without MetaTrader5 (the test supplies a sentinel entry value + plain deal stubs).

**Refactor `dashboard/remote_probe.py`** to import `realized_by_magic` instead of its
inline loop (DRY; remote_probe's JSON output unchanged).

### 2. `scripts/pnl_report.py` — `--broker` mode

- New args: `--broker` (flag), `--since YYYY-MM-DD` (default `2026-06-12`, matching
  remote_probe).
- When `--broker`: SSH `mt5-vps`, run a small inline on the VPS venv that calls
  `realized_by_magic(history_deals_get(frm, to))` and prints JSON; `pnl_report` parses
  it and prints a labeled table:
  ```
  magic  label                      net      gross     swap    n   win%
  1001   portfolio (all books)   +12.30   +20.47   -8.17   34   47%
  2003   consolidation passive   -3.10    -2.55    -0.55   18   44%
  ```
  Unknown magics print with the raw number as the label.
- Without `--broker`: unchanged offline per-strategy portfolio attribution.
- SSH failure (host down / NSG): print a clear message and exit non-zero, do not crash.

### 3. Test: `tests/test_broker_pnl.py`

Unit-test `realized_by_magic` with plain deal stubs (no MetaTrader5):
- only `entry == ENTRY_OUT` deals counted (an IN deal ignored);
- `net = profit + commission + swap`; `gross = profit`; `swap` summed;
- grouping by magic; `wins` counts net > 0;
- empty input → empty dict.

## Granularity note

Broker deals group by **magic**, so the portfolio is one line (1001 = all 6 books
net) and the passive book is its own line (2003). The per-strategy split of the 6
portfolio books stays log-based (they share magic 1001). `--broker` therefore *adds*
the passive book + book-group realized totals (incl. swap/commission), complementing
the existing per-strategy mark-to-market view.

## Out of scope (YAGNI)

- No change to the offline default report.
- No new VPS probe script (reuse the SSH path + the shared function).
- No per-strategy split of magic 1001 from broker deals (impossible — shared magic).
- No change to how the passive runner logs.
