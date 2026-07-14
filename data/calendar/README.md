# `data/calendar/` — economic event calendar

Feeds the defensive **blackout overlay** (`config/risk.yaml` → `blackout`). The
runner forces flat from `before_min` before to `after_min` after any matching
event; the backtest applies the same mute via `scripts/eval_blackout.py`.

Default path: `events.parquet` (CSV also accepted by `load_events`).

## Schema

| column | type | notes |
|--------|------|-------|
| `ts` | datetime, **UTC** | event release time |
| `impact` | str | `high` / `medium` / `low` |
| `currency` | str | e.g. `USD` (gold tracks USD) |
| `title` | str | free text, e.g. `CPI m/m` |

Example rows (illustrative — **not** real dates; populate with a real feed):

```csv
ts,impact,currency,title
2026-06-23T13:45:00Z,high,USD,Flash Manufacturing PMI
2026-06-26T12:30:00Z,high,USD,Core PCE m/m
```

## Sourcing

Primary path: `scripts/fetch_calendar.py` — pulls ForexFactory's own JSON feed
and writes high-impact USD events here (merges + de-dupes on each run).

```bash
uv run scripts/fetch_calendar.py        # default: impact>=high, currency USD
```

The feed publishes the **current week only**, so:

- **Cadence = weekly, not monthly.** A monthly pull would miss ~3 of every 4
  weeks. Run weekly (cron); the merge accumulates forward history. Endpoint
  rate-limits — one pull/day max.
- **Backtest history** (May 12 – Jun 11 2026, to run `eval_blackout.py`) predates
  any forward pulling and is **not** in this feed. For the backtest gate on that
  window, source it once from ForexFactory HTML week archives, Financial Modeling
  Prep, or TradingEconomics, mapped to the schema above.

Scheduled event *times* are known in advance, so pulling uses no future
information. Keep only what moves gold (US macro: FOMC, CPI, PCE, NFP, ISM,
Powell) — the default `high`/`USD` filter does this.

This directory is data, not code — the filtered snapshot is small; commit it so
the VPS gets it on `git pull` (one-way flow), or fetch on the VPS directly.
