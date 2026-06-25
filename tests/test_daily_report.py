import json

from scripts.daily_report import load_today_steps


def test_load_today_steps_filters_by_utc_date(tmp_path):
    log = tmp_path / "portfolio.jsonl"
    rows = [
        {"event": "STEP", "ts": "2026-06-24T23:59:00+00:00", "books": [], "fills": {}, "equity": 1.0},
        {"event": "STEP", "ts": "2026-06-25T08:00:00+00:00", "books": [], "fills": {}, "equity": 2.0},
        {"event": "HEARTBEAT", "ts": "2026-06-25T08:01:00+00:00"},
        {"event": "STEP", "ts": "2026-06-25T20:00:00+00:00", "books": [], "fills": {}, "equity": 3.0},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows))
    out = load_today_steps(log, "2026-06-25")
    assert [s["equity"] for s in out] == [2.0, 3.0]
