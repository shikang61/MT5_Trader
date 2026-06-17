from datetime import datetime, timezone

from mt5_trader.risk.manager import RiskManager, Posture

# size/kill unit posture (mirrors the old round1 numbers so size asserts hold)
POSTURE = Posture(max_leverage=3.0, loss_limit=0.05, target_vol=0.25)
T = datetime(2026, 6, 22, 12, tzinfo=timezone.utc)
T_NEXT = datetime(2026, 6, 23, 9, tzinfo=timezone.utc)


def mk(p=POSTURE):
    return RiskManager(p)


def test_vol_targeting_and_cap():
    rm = mk()
    assert abs(rm.size(1.0, 0.50, T) - 0.5) < 1e-9   # high vol -> scaled below cap
    assert rm.size(1.0, 0.01, T) == 3.0              # low vol -> capped at max_leverage
    assert rm.size(-1.0, 0.01, T) == -3.0


def test_kill_floor_from_day_start():
    rm = mk()
    rm.roll_day(T, 1_000_000)
    assert not rm.kill(990_000, T)   # -1%, fine
    assert rm.kill(940_000, T)       # -6% > 5% limit


def test_kill_without_anchor_is_safe():
    assert not mk().kill(900_000, T)   # no roll_day yet -> no anchor -> no trip


def test_kill_sticky_within_day():
    rm = mk()
    rm.roll_day(T, 1_000_000)
    assert rm.kill(940_000, T)         # trip, stand down
    assert rm.kill(1_000_000, T)       # recovered intraday but still halted today


def test_daily_reset_auto_resume():
    rm = mk()
    rm.roll_day(T, 1_000_000)
    assert rm.kill(940_000, T)         # trip on day T
    rm.roll_day(T_NEXT, 1_000_000)     # new UTC day -> reset anchor + clear halt
    assert not rm.kill(990_000, T_NEXT)  # -1% from new day-start -> resumed


def test_trailing_kill_protects_peak():
    rm = mk(Posture(max_leverage=3.0, loss_limit=0.10, target_vol=0.25, give_back=0.05))
    rm.roll_day(T, 1_000_000)
    assert not rm.kill(1_100_000, T)   # +10%, sets the peak
    assert not rm.kill(1_060_000, T)   # -3.6% from peak, within give_back
    assert rm.kill(1_040_000, T)       # -5.5% from 1.1M peak -> trailing kill
    #   ...even though still +4% on day-start (loss_limit 10% not hit)


def test_give_back_off_by_default():
    rm = mk()  # POSTURE has no give_back -> 0.0
    rm.roll_day(T, 1_000_000)
    rm.kill(1_200_000, T)              # peak 1.2M, no trip
    assert not rm.kill(1_150_000, T)   # -4.2% from peak but give_back off; +15% floor not hit


def test_from_yaml_reads_standing_posture():
    rm = RiskManager.from_yaml("config/risk.yaml")
    assert rm.posture.max_leverage == 2.0
    assert rm.posture.target_vol == 0.30
    assert rm.posture.loss_limit == 0.04
    assert rm.posture.give_back == 0.0
