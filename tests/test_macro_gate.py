from datetime import datetime, timedelta, timezone
import numpy as np
import polars as pl
from mt5_trader.strategies.macro_gate import macro_signal, macro_gate

T0 = datetime(2026, 5, 12, tzinfo=timezone.utc)
def _bars(eur):
    return pl.DataFrame({"ts": [T0 + timedelta(minutes=i) for i in range(len(eur))],
                         "eur_close": [float(x) for x in eur]})

def test_gate_vetoes_disagreeing_only():
    book = np.array([1.0, 1.0, -1.0, -1.0, 0.0])
    mdir = np.array([1.0, -1.0, 1.0, -1.0, 1.0])   # +1 permits long, -1 permits short
    out = macro_gate(book, mdir)
    # bar0 long & permit-long -> keep; bar1 long & permit-short -> veto 0;
    # bar2 short & permit-long -> veto 0; bar3 short & permit-short -> keep; bar4 flat -> 0
    assert out.tolist() == [1.0, 0.0, 0.0, -1.0, 0.0]

def test_gate_neutral_passes_through():
    book = np.array([1.0, -1.0, 1.0])
    mdir = np.array([0.0, 0.0, 0.0])     # neutral -> no veto
    assert macro_gate(book, mdir).tolist() == [1.0, -1.0, 1.0]

def test_gate_never_opens_or_flips():
    book = np.array([0.0, 1.0])
    mdir = np.array([1.0, -1.0])
    out = macro_gate(book, mdir)
    assert out[0] == 0.0          # never opens a position the book didn't ask for
    assert out[1] == 0.0          # disagree -> zero, never flips to -1

def test_macro_signal_rising_eur_is_plus_one():
    # steadily rising EURUSD -> positive slope -> macro_dir = +1 at the end
    eur = list(np.linspace(1.10, 1.12, 300))
    md = macro_signal(_bars(eur), trend_span=50, slope_lag=20, band=0.0)
    assert md[-1] == 1.0

def test_macro_signal_falling_eur_is_minus_one():
    eur = list(np.linspace(1.12, 1.10, 300))
    md = macro_signal(_bars(eur), trend_span=50, slope_lag=20, band=0.0)
    assert md[-1] == -1.0

def test_macro_signal_band_neutralizes_small_slope():
    # tiny drift, large band -> neutral (0)
    eur = list(np.linspace(1.1000, 1.1002, 300))
    md = macro_signal(_bars(eur), trend_span=50, slope_lag=20, band=0.01)  # 1% band
    assert md[-1] == 0.0

def test_macro_signal_no_lookahead_prefix_stable():
    rng = np.random.default_rng(0)
    eur = list(1.10 + np.cumsum(rng.normal(0, 1e-4, 400)))
    bars = _bars(eur)
    full = macro_signal(bars, trend_span=50, slope_lag=20, band=0.0)
    trunc = macro_signal(bars[:200], trend_span=50, slope_lag=20, band=0.0)
    assert np.allclose(full[:200], trunc, equal_nan=True)
