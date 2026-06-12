"""Tick-level microstructure features from quotes + 5-level depth.

Apply to canonical tick frames, then aggregate to bars, e.g.:

    ticks = with_micro_features(load_ticks(...))
    bars = time_bars(ticks, "1m", extra={
        "ofi": pl.col("ofi").sum(),
        "l1_imbalance": pl.col("l1_imbalance").mean(),
        "depth_imbalance": pl.col("depth_imbalance").mean(),
    })
"""
from __future__ import annotations

import polars as pl

from ..data.schema import DEPTH_LEVELS


def with_micro_features(ticks: pl.DataFrame) -> pl.DataFrame:
    b, a = pl.col("bid"), pl.col("ask")
    bs, az = pl.col("bid_sz"), pl.col("ask_sz")

    df = ticks.with_columns(
        mid=(b + a) / 2,
        spread=a - b,
        microprice=(b * az + a * bs) / (bs + az),
        l1_imbalance=(bs - az) / (bs + az),
    )

    # 5-level depth imbalance, if depth columns are present
    if f"bid_sz_{DEPTH_LEVELS}" in df.columns:
        bid_depth = sum(pl.col(f"bid_sz_{i}") for i in range(1, DEPTH_LEVELS + 1))
        ask_depth = sum(pl.col(f"ask_sz_{i}") for i in range(1, DEPTH_LEVELS + 1))
        df = df.with_columns(
            depth_imbalance=(bid_depth - ask_depth) / (bid_depth + ask_depth)
        )

    # Order-flow imbalance (Cont, Kukanov & Stoikov 2014), best level
    bp, ap = b.shift(1), a.shift(1)
    bsp, azp = bs.shift(1), az.shift(1)
    ofi_bid = pl.when(b > bp).then(bs).when(b == bp).then(bs - bsp).otherwise(-bsp)
    ofi_ask = pl.when(a < ap).then(-az).when(a == ap).then(azp - az).otherwise(azp)
    df = df.with_columns(ofi=(ofi_bid + ofi_ask).fill_null(0.0))

    # Tick-rule trade sign proxy on mid changes (0 = no change, carry last sign)
    sign = (pl.col("mid").diff().sign()
            .replace(0, None).forward_fill().fill_null(0))
    return df.with_columns(tick_sign=sign)
