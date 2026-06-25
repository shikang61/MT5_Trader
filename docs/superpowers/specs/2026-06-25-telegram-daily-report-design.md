# Daily Telegram performance report — design

**Date:** 2026-06-25
**Status:** approved (design), pending implementation plan

## Goal

At each weekday gold-session close, push a performance summary to the user over
Telegram so the daily review (currently a manual `fetch_logs` + `pnl_report` +
`check_churn` sequence) arrives automatically. The message reports: total day
P&L, per-strategy P&L breakdown (passive book + each portfolio strategy),
per-strategy win rate, biggest win, biggest loss, total trades, and equity
change.

## Key constraint that shapes the report

Broker realized P&L is tagged only by **magic number**. All portfolio strategies
(Donchian, Vwap, London, Sweep, Ratio, Asian) trade under **one magic = 1001**;
only the passive book has its own magic (2003). So *broker truth* can only split
into two lines: "portfolio (all books)" + "passive consolidation".

The per-strategy breakdown the user wants exists **only** as *modeled* P&L
reconstructed from the `STEP` events in `portfolio.jsonl`
(`backtest/attribution.py`): gross, before costs, mark-to-market — it will not
exactly tie to account equity.

**Decision (hybrid):** the report shows broker-true realized net + equity change
as the headline (real money), then a clearly-labeled per-strategy section from
modeled attribution (gross, pre-cost) for directional color.

## Approach (chosen)

**Self-contained VPS script.** `scripts/daily_report.py` runs *on* the VPS,
reads the local `logs/portfolio.jsonl` (today slice → modeled attribution) and
queries the already-running, logged-in MT5 terminal directly (today's broker
deals + account equity), formats the message, and POSTs it to Telegram. Fired by
an S4U scheduled task at 21:10 UTC, weekdays only.

Mirrors the existing `dashboard/remote_probe.py` (attach to live terminal, read
broker truth) and the weekly calendar-fetch S4U task. No new dependencies —
`urllib.request` (used by `fetch_calendar.py`/`fetch_binance.py`) and
`python-dotenv` (used by `run_portfolio.py`) already exist.

Rejected alternatives:
- **Mac-side over SSH** — Mac is not reliably awake at 21:10 UTC and needs an NSG
  IP re-pin whenever the home IP drifts (a recurring failure mode). Fragile.
- **Report emitted inside the portfolio runner** — couples Telegram I/O into the
  trading hot loop; a send failure must not risk the trader. Keep it
  out-of-process.

## Components

Thin I/O entrypoint over pure, independently testable cores.

| Unit | Change | Responsibility |
|------|--------|----------------|
| `mt5_trader/live/broker_pnl.py::realized_by_magic` | extend | add `best` / `worst` (max/min realized deal `net_d`) per magic, alongside existing net/gross/swap/n/wins. Pure. |
| `mt5_trader/backtest/attribution.py::attribute` | extend | per-book `best` / `worst` trade P&L, tracked in `close_trade` (already counts trades/wins). Pure. |
| `mt5_trader/live/report.py` | new | `build_report(...) -> str`: pure formatter producing the Telegram message text. Golden-string unit-tested. |
| `mt5_trader/live/telegram_send.py` | new | `send(text, token, chat_id)`: thin `urllib` POST to `https://api.telegram.org/bot<token>/sendMessage`. |
| `scripts/daily_report.py` | new | VPS entrypoint: connect MT5 → today's deals + equity → read local log today-slice → `attribute` → `build_report` → `send`. `--dry-run` prints instead of sending. |

## Data flow

Day window = today 00:00 UTC → fire time (21:10 UTC).

1. **Broker truth** (from the live terminal, deals since 00:00 UTC today):
   - Total day P&L = Σ realized net across magics.
   - Per magic: net, n (trades), win%, best, worst.
   - Magic 1001 → "Portfolio (all books)"; magic 2003 → "Passive consolidation".
2. **Equity change** = `account_info().equity` − first `STEP` equity of today
   (read from `portfolio.jsonl`). Shown separately from realized P&L because it
   includes floating/open positions.
3. **Modeled per-strategy** (from `portfolio.jsonl` `STEP` events, today slice,
   via the extended `attribute`): per portfolio book — gross_pnl, win%
   (wins/trades), best, worst, trades. Labeled "modeled gross, pre-cost".

## Message format

Telegram message, monospace block for alignment (numbers illustrative):

```
🟡 GOLD DESK — 2026-06-25 (close 21:10 UTC)
Equity  99,236 → 100,025   (+789.13)

REALIZED today (broker truth, net):
  Portfolio (all books)   −46.08   16 tr  50% W   best +X  worst −Y
  Passive consolidation   +29.44    7 tr  57% W   best +X  worst −Y
  ── TOTAL realized        −16.64

PER-STRATEGY (modeled gross, pre-cost — won't tie to equity):
  Donchian   +2126   61% W   best +410  worst −180   42 tr
  London      +107   ...
  Vwap        −170   ...
```

## Error handling

- **MT5 connect fail / zero deals:** still send, noting "no broker data" — silence
  is ambiguous (could mean "no trades" or "script died").
- **Telegram POST fail:** write the error to a log file and exit nonzero so the
  watchdog surfaces it.
- **Missing today steps in log:** modeled section prints "no steps today".
- **Weekend / holiday:** the scheduled task is weekday-only; if the script still
  fires with zero deals and zero steps, it sends a short "flat day" note.

## Secrets & scheduling

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` live in the VPS `.env`, alongside
  the MT5 credentials (`load_dotenv()` in the script). The user creates the bot
  via @BotFather and supplies the token + numeric chat_id.
- New S4U scheduled task **`MT5 Daily Report`**:
  `schtasks /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 21:10` running
  `.venv\Scripts\python.exe scripts\daily_report.py`. VPS clock is UTC, so 21:10
  local = 21:10 UTC. Single-instance safe (read-only; no trading).
- Document the new task in `memory/vps-runtime-setup` and `docs/MAC_MINI_SETUP.md`
  (alongside the calendar-fetch task) so it survives a rebuild.

## Testing

- **Unit (no MT5/Telegram):** `realized_by_magic` best/worst from synthetic
  deals; `attribute` best/worst from synthetic `STEP` steps; `build_report`
  golden string from a fixed deals+attribution input.
- **Live smoke:** `uv run scripts/daily_report.py --dry-run` on the VPS against
  today's real data, eyeball the message, then enable the scheduled task.

## Out of scope

- Charts/graphics (text only).
- Weekly roll-up or per-hour breakdown (daily only).
- Splitting portfolio books at the broker (would need per-book magics — a larger
  execution change, not pursued here).
- Two-way bot commands (push only).
