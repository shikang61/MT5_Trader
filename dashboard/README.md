# Live dashboard

Streamlit dashboard for live performance + strategy breakdown of the portfolio bot.

```bash
uv run --with streamlit --with plotly streamlit run dashboard/app.py
```

Opens in the browser, auto-refreshes (sidebar interval). Each refresh pulls
**broker truth** + the decision log off the VPS over read-only SSH.

Offline preview (no SSH — renders the last cached pull in `dashboard/.cache/`):

```bash
MT5_DASH_DEMO=1 uv run --with streamlit --with plotly streamlit run dashboard/app.py
```

## Panels
1. **Equity curve** — account equity over time, day P&L, vs start / kill-switch distance.
2. **Realized P&L by strategy** — broker closing deals grouped by magic (portfolio 1001,
   consolidation 2003, …) + per-book gross attribution inside the portfolio (london_orb,
   vwap_m5, donchian, ratio_mr, sweep_fade) from the decision log.
3. **Open positions + signal/regime** — live positions per symbol, each book's latest
   signal/target, and the XAU/XAG ratio-spread regime.
4. **Fill quality** — live decision vs the completed-bar backtest signal per book; ~100%
   means the forming-bar fix is holding.

## How it works
- `remote_probe.py` runs **on the VPS** (same MT5 gateway the bot uses): account,
  positions, deals-by-magic, and the bars the bot signals on → one JSON blob.
- `sources.py` (Mac) runs the probe over SSH and scp's `logs/portfolio.jsonl` to
  `dashboard/.cache/`, then builds the panel frames (reusing `mt5_trader` attribution,
  regime, strategies).
- `app.py` renders it.

## Requirements
- SSH access to `mt5-vps`. If a refresh times out, the Azure NSG pin went stale
  (home IP changed): `bash scripts/provision_azure.sh ip`.
- The portfolio bot + MT5 terminal running on the VPS.
