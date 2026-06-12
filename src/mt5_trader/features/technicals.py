"""Bar-level technical features (polars expressions / helpers)."""
from __future__ import annotations

import math

import polars as pl


def ema(col: str, span: int) -> pl.Expr:
    return pl.col(col).ewm_mean(span=span)


def zscore(col: str, n: int) -> pl.Expr:
    x = pl.col(col)
    return (x - x.rolling_mean(n)) / x.rolling_std(n)


def rsi(col: str, n: int = 14) -> pl.Expr:
    d = pl.col(col).diff()
    gain = d.clip(lower_bound=0).ewm_mean(span=n)
    loss = (-d).clip(lower_bound=0).ewm_mean(span=n)
    return 100 - 100 / (1 + gain / loss)


def atr(n: int = 14) -> pl.Expr:
    pc = pl.col("close").shift(1)
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - pc).abs(),
        (pl.col("low") - pc).abs(),
    )
    return tr.rolling_mean(n)


def realized_vol(n: int, bars_per_year: float) -> pl.Expr:
    """Annualized close-to-close vol over a rolling window of n bars."""
    r = pl.col("close").log().diff()
    return r.rolling_std(n) * math.sqrt(bars_per_year)


def add_sessions(bars: pl.DataFrame) -> pl.DataFrame:
    """UTC session tags. BST = UTC+1 during the competition."""
    h = pl.col("ts").dt.hour()
    return bars.with_columns(
        session=pl.when(h < 7).then(pl.lit("asia"))
        .when(h < 12).then(pl.lit("london"))
        .when(h < 21).then(pl.lit("ny"))
        .otherwise(pl.lit("late"))
    )
