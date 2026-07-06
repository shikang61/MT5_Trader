"""VPS watchdog: freshness + bad-event detection with dedupe/recovery."""
import importlib.util
import json
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "vps_watchdog", Path(__file__).parents[1] / "scripts" / "vps_watchdog.py")
wd = importlib.util.module_from_spec(spec)
sys.modules["vps_watchdog"] = wd
spec.loader.exec_module(wd)


def setup_log(tmp_path, monkeypatch, name="portfolio.jsonl", limit=300):
    monkeypatch.setattr(wd, "WATCH", {f"{tmp_path}/{name}": limit})
    p = tmp_path / name
    return p


def write_events(p: Path, events):
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_fresh_log_no_alert(tmp_path, monkeypatch):
    p = setup_log(tmp_path, monkeypatch)
    write_events(p, [{"event": "STEP", "ts": "2026-07-06T00:00:00+00:00"}])
    alerts, recoveries, _ = wd.check(time.time(), {})
    assert alerts == [] and recoveries == []


def test_stale_log_alerts_once_then_dedupes(tmp_path, monkeypatch):
    p = setup_log(tmp_path, monkeypatch)
    write_events(p, [{"event": "STEP", "ts": "t1"}])
    now = p.stat().st_mtime + 9999
    alerts, _, state = wd.check(now, {})
    assert len(alerts) == 1 and "stale" in alerts[0]
    alerts2, _, state2 = wd.check(now + 60, state)      # 1 min later: deduped
    assert alerts2 == []
    alerts3, _, _ = wd.check(now + wd.REALERT_MIN * 60 + 1, state2)  # re-page
    assert len(alerts3) == 1


def test_recovery_message_after_stale(tmp_path, monkeypatch):
    p = setup_log(tmp_path, monkeypatch)
    write_events(p, [{"event": "STEP", "ts": "t1"}])
    stale_now = p.stat().st_mtime + 9999
    _, _, state = wd.check(stale_now, {})
    p.touch()                                            # loop resumed
    alerts, recoveries, state2 = wd.check(time.time(), state)
    assert alerts == [] and len(recoveries) == 1
    _, recoveries2, _ = wd.check(time.time(), state2)    # all-clear only once
    assert recoveries2 == []


def test_missing_log_alerts(tmp_path, monkeypatch):
    setup_log(tmp_path, monkeypatch, name="never_created.jsonl")
    alerts, _, _ = wd.check(time.time(), {})
    assert len(alerts) == 1 and "missing" in alerts[0]


def test_bad_events_reported_once(tmp_path, monkeypatch):
    p = setup_log(tmp_path, monkeypatch)
    write_events(p, [
        {"event": "STEP", "ts": "2026-07-06T00:00:00+00:00"},
        {"event": "ERROR", "ts": "2026-07-06T00:01:00+00:00", "error": "boom"},
        {"event": "KILL_SWITCH", "ts": "2026-07-06T00:02:00+00:00", "equity": 90000},
    ])
    alerts, _, state = wd.check(time.time(), {})
    assert len(alerts) == 2
    assert any("ERROR" in a and "boom" in a for a in alerts)
    assert any("KILL_SWITCH" in a for a in alerts)
    alerts2, _, _ = wd.check(time.time(), state)         # high-water mark holds
    assert alerts2 == []


def test_new_bad_event_after_hwm_alerts(tmp_path, monkeypatch):
    p = setup_log(tmp_path, monkeypatch)
    write_events(p, [{"event": "ERROR", "ts": "2026-07-06T00:01:00+00:00", "error": "a"}])
    _, _, state = wd.check(time.time(), {})
    write_events(p, [
        {"event": "ERROR", "ts": "2026-07-06T00:01:00+00:00", "error": "a"},
        {"event": "ERROR", "ts": "2026-07-06T00:05:00+00:00", "error": "b"},
    ])
    alerts, _, _ = wd.check(time.time(), state)
    assert len(alerts) == 1 and "b" in alerts[0]
