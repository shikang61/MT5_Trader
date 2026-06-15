from datetime import datetime, timezone

from mt5_trader.risk.manager import RiskManager, RoundCfg

R1 = RoundCfg("round1",
              datetime(2026, 6, 21, 22, tzinfo=timezone.utc),
              datetime(2026, 6, 22, 21, tzinfo=timezone.utc),
              max_leverage=3.0, loss_limit=0.05, target_vol=0.25)
DEFAULT = RoundCfg("default", None, None,
                   max_leverage=2.0, loss_limit=0.04, target_vol=0.15)

IN_R1 = datetime(2026, 6, 22, 12, tzinfo=timezone.utc)
OUTSIDE = datetime(2026, 6, 18, 12, tzinfo=timezone.utc)


def mk():
    return RiskManager([R1], DEFAULT)


def test_round_selection():
    rm = mk()
    assert rm.round_at(IN_R1).name == "round1"
    assert rm.round_at(OUTSIDE).name == "default"


def test_vol_targeting_and_cap():
    rm = mk()
    # high vol -> scaled down below cap
    assert abs(rm.size(1.0, 0.50, IN_R1) - 0.5) < 1e-9
    # low vol -> capped at max_leverage
    assert rm.size(1.0, 0.01, IN_R1) == 3.0
    assert rm.size(-1.0, 0.01, IN_R1) == -3.0


def test_kill_switch():
    rm = mk()
    rm.mark_round_start(IN_R1, 1_000_000)
    assert not rm.kill(990_000, IN_R1)   # -1%, fine
    assert rm.kill(940_000, IN_R1)       # -6% > 5% limit


def test_kill_without_round_start_is_safe():
    assert not mk().kill(900_000, IN_R1)


def test_trailing_kill_protects_peak():
    r = RoundCfg("r", R1.start, R1.end, max_leverage=3.0,
                 loss_limit=0.10, target_vol=0.25, give_back=0.05)
    rm = RiskManager([r], DEFAULT)
    rm.mark_round_start(IN_R1, 1_000_000)
    assert not rm.kill(1_100_000, IN_R1)   # +10%, sets the peak
    assert not rm.kill(1_060_000, IN_R1)   # -3.6% from peak, within give_back
    assert rm.kill(1_040_000, IN_R1)       # -5.5% from 1.1M peak -> trailing kill
    #   ...even though still +4% on round-start (loss_limit 10% not hit)


def test_give_back_off_by_default():
    rm = mk()  # R1 has no give_back -> 0.0
    rm.mark_round_start(IN_R1, 1_000_000)
    rm.kill(1_200_000, IN_R1)              # peak 1.2M
    assert not rm.kill(1_150_000, IN_R1)   # -4% from peak but give_back off, floor not hit
