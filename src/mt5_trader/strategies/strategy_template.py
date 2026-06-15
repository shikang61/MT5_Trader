"""TEMPLATE — duplicate this file to create a new strategy. Not registered.

How to use:
  1. Copy:  cp strategy_template.py my_strategy.py   (inside this directory)
  2. Rename the class, set a unique `name`, define your params as fields.
  3. Write signal(): bars in, target position per bar out.
  4. Uncomment the @register line.
  5. Add to __init__.py imports:  from . import ..., my_strategy
  6. Check + backtest:
       uv run pytest
       uv run scripts/run_backtest.py --strategy my_name --symbol XAUUSD
  7. Gates before trusting it (docs/WORKFLOW.md Stage 3): walk-forward,
     --slippage-bps 2 stress, params ±25%.

Contract:
  - signal(bars) returns np.ndarray, same length as bars, values in [-1, 1].
    +1 = full long, -1 = full short, 0 = flat. Fractions = partial size.
  - signal[t] is decided on bar t's CLOSE; the engine/live runner fill it
    one bar later. Use only data up to bar t: rolling windows and .shift(1)
    are fine, .shift(-1) or any future data = lookahead = broken.
  - bars columns: ts (UTC), open, high, low, close, spread_mean, n_ticks.
    `ofi` is present too when bars are built with the OFI aggregation
    (run_backtest.py does this by default).
  - Building blocks: ..features.technicals (ema, atr, rsi, zscore,
    realized_vol, add_sessions), ..features.microstructure (tick-level).
  - References: momentum_breakout.py (state machine), session_vol.py
    (time-of-day), orderflow_imbalance.py (feature z-score + holding period),
    meanrev_pairs.py (two-leg spread).
"""
from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register  # noqa: F401  (used once you uncomment @register)


# @register
@dataclass
class MyStrategy:
    # --- tunable parameters (defaults assume 1-minute bars) ---
    lookback: int = 60
    z_entry: float = 2.0

    name = "my_name"  # unique; this is the --strategy CLI argument

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        # EXAMPLE BODY (delete and write your own):
        # mean reversion on the z-score of 1-bar log returns.
        r = pl.col("close").log().diff()
        z = (r - r.rolling_mean(self.lookback)) / r.rolling_std(self.lookback)
        out = bars.select(
            pos=(-z.clip(-self.z_entry, self.z_entry) / self.z_entry)
        )
        return out["pos"].fill_null(0.0).to_numpy()
