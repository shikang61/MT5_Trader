from datetime import datetime, timezone

from scripts.daily_report import load_weights, session_bounds


def test_session_starts_at_prior_22utc_before_evening():
    # 13:00 UTC is inside the session that opened the PREVIOUS day at 22:00
    now = datetime(2026, 6, 25, 13, 0, tzinfo=timezone.utc)
    start, end = session_bounds(now)
    assert start == datetime(2026, 6, 24, 22, 0, tzinfo=timezone.utc)
    assert end == now


def test_session_starts_same_day_after_22utc():
    # 22:30 UTC: the new session has opened today at 22:00
    now = datetime(2026, 6, 25, 22, 30, tzinfo=timezone.utc)
    start, _ = session_bounds(now)
    assert start == datetime(2026, 6, 25, 22, 0, tzinfo=timezone.utc)


def test_load_weights_sorted_heaviest_first(tmp_path):
    cfg = tmp_path / "portfolio.yaml"
    cfg.write_text(
        "books:\n"
        "  - strategy: donchian\n    weight: 0.40\n"
        "  - strategy: london_orb\n    weight: 0.10\n"
        "  - strategy: sweep_fade\n    weight: 0.15\n"
    )
    assert load_weights(cfg) == [("donchian", 0.40), ("sweep_fade", 0.15), ("london_orb", 0.10)]


def test_load_weights_missing_file_returns_empty(tmp_path):
    assert load_weights(tmp_path / "nope.yaml") == []
