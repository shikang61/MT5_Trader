#!/usr/bin/env python
"""VPS-side runner watchdog -> Telegram. Runs ON the VPS every 5 min (scheduled
task "MT5 Watchdog"), so it alerts even when the Mac is asleep — the gap the
Mac-side scripts/watchdog.py leaves (2026-07-06: portfolio loop silently down
after a restart race; nothing alerted).

Checks per monitored log:
  - freshness: the runner heartbeats every ~60s even in a quiet market, so a
    stale mtime means the loop is dead (or the process is wedged).
  - bad events: ERROR / KILL_SWITCH / RECONNECT_FAILED / HALTED lines newer
    than the last run's high-water mark.

Alerts via the daily-report Telegram creds (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
user env). Dedupe: a stale log alerts once per REALERT_MIN while it stays
stale (state in logs/watchdog_state.json); recovery sends an all-clear.

    .venv\\Scripts\\python.exe scripts\\vps_watchdog.py            # one check
    .venv\\Scripts\\python.exe scripts\\vps_watchdog.py --dry-run  # print, no send
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mt5_trader.live import telegram_send  # noqa: E402

# log file -> stale threshold (seconds). Portfolio is the money loop (heartbeat
# ~60s); the tick logger flushes every 60s; passive books heartbeat like the
# portfolio. Generous thresholds so one slow poll can't page.
WATCH = {
    "logs/portfolio.jsonl": 300,
    "logs/log_ticks.jsonl": 300,
    "logs/passive_consolidation.jsonl": 600,
}
BAD_EVENTS = {"ERROR", "KILL_SWITCH", "RECONNECT_FAILED", "HALTED"}
STATE = Path("logs/watchdog_state.json")
REALERT_MIN = 30          # re-page for a still-stale log every N minutes
TAIL_LINES = 120


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def tail_bad_events(path: Path, since_ts: str) -> tuple[list[str], str]:
    """Bad-event lines newer than since_ts, plus the new high-water ts."""
    problems, hwm = [], since_ts
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    except OSError:
        return problems, hwm
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = ev.get("ts", "")
        if ts > hwm:
            hwm = ts
        if ev.get("event") in BAD_EVENTS and ts > since_ts:
            detail = ev.get("error") or ev.get("equity") or ""
            problems.append(f"{ev['event']} {ts} {str(detail)[:120]}")
    return problems, hwm


def check(now: float, state: dict) -> tuple[list[str], list[str], dict]:
    """-> (alerts to send, recoveries to send, next state)."""
    alerts, recoveries = [], []
    nxt = {"stale": dict(state.get("stale", {})), "hwm": dict(state.get("hwm", {}))}
    for rel, limit in WATCH.items():
        p = Path(rel)
        name = p.name
        if not p.exists():
            age = None
        else:
            age = int(now - p.stat().st_mtime)
        stale = age is None or age > limit
        last_alert = nxt["stale"].get(name)          # epoch of last stale page
        if stale:
            if last_alert is None or now - last_alert >= REALERT_MIN * 60:
                what = "missing" if age is None else f"stale {age}s"
                alerts.append(f"{name} {what} (limit {limit}s) — loop dead?")
                nxt["stale"][name] = now
        else:
            if last_alert is not None:
                recoveries.append(f"{name} fresh again (age {age}s)")
                del nxt["stale"][name]
            probs, hwm = tail_bad_events(p, nxt["hwm"].get(name, ""))
            nxt["hwm"][name] = hwm
            alerts.extend(f"{name}: {m}" for m in probs)
    return alerts, recoveries, nxt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, don't send")
    args = ap.parse_args()
    os.chdir(Path(__file__).resolve().parents[1])

    state = load_state()
    alerts, recoveries, nxt = check(time.time(), state)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(nxt))

    msgs = []
    if alerts:
        msgs.append("MT5 VPS watchdog — PROBLEM\n" + "\n".join(alerts))
    if recoveries:
        msgs.append("MT5 VPS watchdog — recovered\n" + "\n".join(recoveries))
    if not msgs:
        print("ok", flush=True)
        return
    for m in msgs:
        print(m, flush=True)
        if not args.dry_run:
            telegram_send.send(os.environ["TELEGRAM_BOT_TOKEN"],
                               os.environ["TELEGRAM_CHAT_ID"], m)


if __name__ == "__main__":
    main()
