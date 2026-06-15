# `strategies/`

Trading books. A strategy is any object with `signal(bars) -> np.ndarray`
returning the target position in `[-1, 1]` per bar (see `base.py`). Each book is
`@register`-ed under a short `name` and resolved through `REGISTRY` by the
backtest engine, `validate_books.py`, and the live runner. Full research
narrative per book: `docs/STRATEGY_PLAYBOOK.md`.

| File | `name` | Description |
|------|--------|-------------|
| `base.py` | — | Strategy `Protocol` + `register`/`REGISTRY`. Signals must use data up to the current bar only (engine shifts execution by one). |
| `__init__.py` | — | Imports every module to populate `REGISTRY`. |
| `momentum_breakout.py` | `donchian` | Donchian channel breakout with EMA trend filter. **Deployed (anchor).** |
| `vwap_trend.py` | `vwap_trend` | Session-VWAP stretch continuation (2σ beyond VWAP continues, not reverts). **Deployed.** |
| `session_vol.py` | `london_orb` | London open range breakout. **Deployed.** |
| `sweep_fade.py` | `sweep_fade` | Prior-day high/low sweep fade ("liquidity sweep" reversal). **Deployed.** |
| `meanrev_pairs.py` | `ratio_mr` | Mean reversion on a synthetic spread (XAU/XAG ratio); RANGE-regime gated. **Deployed.** Includes `spread_bars()` helper. |
| `regime_switch.py` | `regime_switch` | Regime-conditional: ride trend / fade range extremes. Range-fade leg off by default. Benched. |
| `orderflow_imbalance.py` | `ofi` | Order-flow imbalance momentum. Benched (needs real L2 depth). |
| `fast_meanrev.py` | `fast_mr` | Rolling-mean z-score fade (state machine). Benched (taker fade, costs eat it; backtest-only). |
| `strategy_template.py` | — | Copy-this-file template for a new strategy. **Not registered.** |

Deployed set + weights: `config/portfolio.yaml`.
