"""Tick -> bar aggregation."""
from __future__ import annotations

import polars as pl


def _with_mid(ticks: pl.DataFrame) -> pl.DataFrame:
    cols = {}
    if "mid" not in ticks.columns:
        cols["mid"] = (pl.col("bid") + pl.col("ask")) / 2
    if "spread" not in ticks.columns:
        cols["spread"] = pl.col("ask") - pl.col("bid")
    return ticks.with_columns(**cols) if cols else ticks


def _agg_exprs(extra: dict[str, pl.Expr] | None) -> dict[str, pl.Expr]:
    exprs = dict(
        open=pl.col("mid").first(),
        high=pl.col("mid").max(),
        low=pl.col("mid").min(),
        close=pl.col("mid").last(),
        close_bid=pl.col("bid").last(),
        close_ask=pl.col("ask").last(),
        spread_mean=pl.col("spread").mean(),
        n_ticks=pl.len(),
    )
    if extra:
        exprs.update(extra)
    return exprs


def time_bars(ticks: pl.DataFrame, every: str = "1m",
              extra: dict[str, pl.Expr] | None = None) -> pl.DataFrame:
    """OHLC bars on the mid price. `extra` adds agg expressions
    (e.g. ofi=pl.col("ofi").sum() after with_micro_features)."""
    return (
        _with_mid(ticks)
        .sort("ts")
        .group_by_dynamic("ts", every=every)
        .agg(**_agg_exprs(extra))
    )


def tick_bars(ticks: pl.DataFrame, n: int = 500,
              extra: dict[str, pl.Expr] | None = None) -> pl.DataFrame:
    """Bars of fixed tick count — more uniform information flow than time bars."""
    return (
        _with_mid(ticks)
        .sort("ts")
        .with_row_index("_i")
        .group_by(pl.col("_i") // n, maintain_order=True)
        .agg(ts=pl.col("ts").last(), **_agg_exprs(extra))
        .drop("_i")
    )
