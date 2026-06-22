"""Regression: polars 1.41 `from_numpy` panics (AsSliceError, aborts the process)
on a length-1 structured ndarray. mt5.copy_ticks_range returns exactly that when a
quiet flush picks up a single tick (killed the VPS tick logger 2026-06-21). The
gateway must convert via `_struct_to_df` without panicking. See mt5_gateway."""
import numpy as np

from mt5_trader.live.mt5_gateway import _struct_to_df

# mt5.copy_ticks_range dtype (COPY_TICKS_ALL)
TICK_DT = np.dtype([("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"),
                    ("volume", "<u8"), ("time_msc", "<i8"), ("flags", "<u4"),
                    ("volume_real", "<f8")])


def make_ticks(n: int) -> np.ndarray:
    a = np.zeros(n, dtype=TICK_DT)
    a["bid"] = np.linspace(4000.0, 4001.0, n) if n else []
    a["ask"] = a["bid"] + 0.1
    a["time_msc"] = np.arange(n) * 1000
    a["volume_real"] = 1.0
    return a


def test_single_row_does_not_panic():
    """The exact crash input: a one-tick batch."""
    df = _struct_to_df(make_ticks(1))
    assert df.height == 1
    assert list(df.columns) == list(TICK_DT.names)
    assert df["bid"][0] == 4000.0


def test_multi_row_values_preserved():
    arr = make_ticks(10)
    df = _struct_to_df(arr)
    assert df.height == 10
    assert np.allclose(df["bid"].to_numpy(), arr["bid"])
    assert np.allclose(df["ask"].to_numpy(), arr["ask"])
    assert df["time_msc"].to_list() == arr["time_msc"].tolist()


def test_empty_batch():
    df = _struct_to_df(make_ticks(0))
    assert df.height == 0
    assert list(df.columns) == list(TICK_DT.names)


def test_strided_view():
    """copy_ticks_range slices can be non-contiguous views."""
    df = _struct_to_df(make_ticks(10)[::2])
    assert df.height == 5
    assert np.allclose(df["bid"].to_numpy(), make_ticks(10)["bid"][::2])
