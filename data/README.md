# data/

Market data used by the research pipeline and live bots. The full dataset
(~3 GB of tick parquets) is **not** committed — this directory is gitignored,
with one small example file per category force-added so the formats are
visible:

| Path | Contents | Schema |
|---|---|---|
| `calendar/events.parquet` | Economic calendar events | `ts, impact, currency, title` |
| `bars_cache/<SYM>_<tf>_*.parquet` | Cached OHLC bars aggregated from ticks | `ts, open, high, low, close, close_bid, close_ask, spread_mean, n_ticks` |
| `processed/<SYM>/duka_<date>.parquet` | Dukascopy top-of-book ticks, one file per day | `ts, symbol, bid, ask, bid_sz, ask_sz` |
| `ticks/<SYM>/<date>_<ms>.parquet` | Live tick snapshots captured by the bots | `ts, symbol, bid, ask, bid_sz, ask_sz, volume` |
| `real/<SYM>/<SYM>_<date>.parquet` | Full live capture incl. depth-of-book arrays, one file per day | `ts, symbol, bid, ask, bid_sz, ask_sz, bidprices, bidsizes, askprices, asksizes` |

To add more files past the gitignore: `git add -f data/<path>`.
