import numpy as np

from mt5_trader.backtest.retest import simulate_retest_entry

# Long-breakout episode: raw nonzero on bars 2..6, exit (->0) at bar 7.
RAW = np.array([0, 0, 1, 1, 1, 1, 1, 0], dtype=float)
HI = np.full(8, 100.0)   # anchor L for a long = entry_hi captured at breakout bar 2
LO = np.full(8, 90.0)    # unused for a long
HIGH = np.full(8, 101.0) # unused for a long
CLOSE = np.full(8, 101.0)


def test_fills_on_touch_priced_at_level():
    # low dips to <=100 on bar 4 (bars 2,3 stay above the level)
    low = np.array([101, 101, 100.5, 100.2, 99.9, 100.1, 100.1, 100.1], dtype=float)
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=5, fallback=False)
    assert list(f.pos) == [0, 0, 0, 0, 1, 1, 1, 0]   # held from the fill bar to the exit
    assert f.entry_px[4] == 100.0                      # priced at L, not the bar close
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 1, 0, 0)


def test_no_fill_on_breakout_bar():
    # breakout bar 2 low is below L, but a fill there would be non-causal -> never fills
    low = np.array([101, 101, 99.0, 100.2, 100.3, 100.4, 100.5, 100.6], dtype=float)
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=5, fallback=False)
    assert not f.pos.any()
    assert (f.n_armed, f.n_filled, f.n_skipped) == (1, 0, 1)


def test_skips_when_no_touch_under_skip_policy():
    low = np.full(8, 100.5)  # never reaches L
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=2, fallback=False)
    assert not f.pos.any()                              # no re-arm mid-run
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 0, 0, 1)


def test_fallback_market_entry_at_deadline():
    low = np.full(8, 100.5)  # never reaches L; deadline is bar 4 (arm bar 2 + max_wait 2)
    f = simulate_retest_entry(RAW, HI, LO, HIGH, low, CLOSE, max_wait=2, fallback=True)
    assert list(f.pos) == [0, 0, 0, 0, 1, 1, 1, 0]
    assert f.entry_px[4] == 101.0                       # market = bar close at the deadline
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 0, 1, 0)


def test_skip_when_exit_fires_before_deadline_even_with_fallback():
    raw = np.array([0, 0, 1, 1, 0, 0, 0, 0], dtype=float)  # exits at bar 4, before deadline
    low = np.full(8, 100.5)  # never touches L
    f = simulate_retest_entry(raw, HI, LO, HIGH, low, CLOSE, max_wait=10, fallback=True)
    assert not f.pos.any()
    assert (f.n_armed, f.n_filled, f.n_fallback, f.n_skipped) == (1, 0, 0, 1)


def test_counts_invariant_on_random_input():
    rng = np.random.default_rng(0)
    n = 500
    raw = np.sign(np.where(rng.random(n) < 0.1, rng.normal(size=n), 0.0))
    px = 100 + np.cumsum(rng.normal(size=n))
    hi = px - 0.5
    lo = px + 0.5  # for shorts (high>=entry_lo)
    f = simulate_retest_entry(raw, hi, lo, px + 0.3, px - 0.3, px,
                              max_wait=10, fallback=False)
    assert f.n_armed == f.n_filled + f.n_fallback + f.n_skipped
    # exits unchanged: a held bar must coincide with a nonzero raw bar
    held = f.pos != 0
    assert np.array_equal(f.pos[held], raw[held])
