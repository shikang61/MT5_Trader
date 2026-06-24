import numpy as np
import polars as pl
import pytest

import mt5_trader.research as research
from mt5_trader.research import (
    SLIP_BP, aligned_fwd, cost_bp, dedupe, fwd_returns, report, sweep_events,
    verdict, _grid_1s,
)


@pytest.fixture(autouse=True)
def _no_real_trial_log(monkeypatch):
    # event-study cells log a trial by default; never write research/trials.jsonl
    # from the test suite.
    monkeypatch.setattr(research, "LOG_TRIALS", False)


def test_cost_bp_bar_columns():
    df = pl.DataFrame({"spread_mean": [0.2, 0.4], "close": [100.0, 100.0]})
    # mean(spread/mid)=0.003 -> 30 bp + 0.5 slip
    assert abs(cost_bp(df) - (30.0 + SLIP_BP)) < 1e-9


def test_cost_bp_grid_columns():
    g = pl.DataFrame({"spread": [0.2, 0.4], "mid": [100.0, 100.0]})
    assert abs(cost_bp(g, spread_col="spread", mid_col="mid") - (30.0 + SLIP_BP)) < 1e-9


def test_fwd_returns_value_and_nan_tail():
    close = np.array([100.0, 110.0, 121.0, 133.1])
    fwd = fwd_returns(close, horizon=1)
    # entry at next bar (index 1), exit one bar later (index 2): log(121/110)
    assert abs(fwd[0] - np.log(121.0 / 110.0)) < 1e-9
    assert np.isnan(fwd[-1]) and np.isnan(fwd[-2])


def test_dedupe_min_gap():
    out = dedupe(np.array([0, 2, 3, 10, 11, 20]), min_gap=5)
    assert out.tolist() == [0, 10, 20]


def test_aligned_fwd_mean_hit_n():
    sig = np.array([1.0, -1.0, 1.0])
    fwd = np.array([0.0010, -0.0020, np.nan])   # bp: +10, +20 (short*neg), nan
    mean_bp, hit, n = aligned_fwd(sig, fwd, np.array([0, 1, 2]))
    assert n == 2
    assert abs(mean_bp - 15.0) < 1e-9            # (+10 + +20)/2
    assert hit == 1.0


def test_aligned_fwd_no_valid_events():
    assert aligned_fwd(np.array([1.0]), np.array([np.nan]), np.array([0])) == (0.0, 0.0, 0)


def test_sweep_events_high_reclaim():
    # level=100 on every bar; quiet small; cross up at bar 3, reclaim 1 bar later
    bars = pl.DataFrame({
        "high":  [99.0, 99.0, 99.0, 101.0, 100.5],
        "low":   [98.0, 98.0, 98.0, 99.0, 99.0],
        "close": [98.5, 98.5, 98.5, 100.6, 99.5],
    })
    levels = np.full(5, 100.0)
    idx, reclaim = sweep_events(bars, levels, side=1, quiet=2, confirm=1)
    assert idx.tolist() == [3]
    assert reclaim.tolist() == [True]            # close[4]=99.5 < 100 -> reclaimed


def test_grid_1s_ffill_shape():
    from datetime import datetime, timezone
    t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    ticks = pl.DataFrame({
        "ts": [t0.replace(second=0), t0.replace(second=0, microsecond=500000),
               t0.replace(second=2)],          # gap at second 1
        "bid": [100.0, 100.2, 101.0],
        "ask": [100.2, 100.4, 101.2],
    })
    g = _grid_1s(ticks)
    assert g["ts"].dt.second().to_list() == [0, 1, 2]   # second 1 filled
    assert abs(g["mid"][1] - g["mid"][0]) < 1e-9        # ffill held second-0 mid


def test_report_pass_fail(capsys):
    report("x", np.array([5.0, 7.0]), hurdle=1.0)
    assert "PASS" in capsys.readouterr().out
    report("y", np.array([-5.0]), hurdle=1.0)
    assert "FAIL" in capsys.readouterr().out


def test_verdict_pass_fail(capsys):
    verdict("a", 5.0, 1.0, 10, 0.6)
    assert "PASS" in capsys.readouterr().out
    verdict("b", 0.1, 1.0, 10, 0.4)
    assert "FAIL" in capsys.readouterr().out


def test_report_and_verdict_log_a_trial_per_cell(monkeypatch):
    # each event-study cell is one tested hypothesis -> one logged trial, so the
    # multiple-testing haircut counts the real search.
    calls = []
    monkeypatch.setattr(research.trials, "log_trial",
                        lambda idea, **k: calls.append((idea, k.get("verdict"))))
    monkeypatch.setattr(research, "LOG_TRIALS", True)
    report("london", np.array([5.0, 7.0]), hurdle=1.0)   # PASS
    verdict("ny", 0.1, 1.0, 10, 0.4)                      # FAIL
    assert calls == [("london", "PASS"), ("ny", "FAIL")]


def test_no_trial_logged_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(research.trials, "log_trial", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(research, "LOG_TRIALS", False)
    report("x", np.array([5.0]), hurdle=1.0)
    verdict("y", 5.0, 1.0, 10, 0.6)
    assert calls == []
