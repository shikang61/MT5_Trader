#!/usr/bin/env python
"""VPS runner watchdog — run on the Mac.

    uv run scripts/watchdog.py              # one check, prints status
    uv run scripts/watchdog.py --loop 300   # check every 5 min, macOS
                                            # notification + sound on problems

Checks over ssh: freshness of the newest logs/*.jsonl (runner heartbeats every
~60s even when the market is quiet) and bad events in the last lines.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time

HOST = "mt5-vps"
LOG_GLOB = r"Desktop\MT5_Trader\logs\*.jsonl"
BAD_EVENTS = {"ERROR", "KILL_SWITCH", "RECONNECT_FAILED", "HALTED"}
STALE_SECONDS = 180

PS = (
    "$f = Get-ChildItem " + LOG_GLOB + " -ErrorAction SilentlyContinue | "
    "Sort-Object LastWriteTime -Descending | Select-Object -First 1; "
    "if ($f) { $age = [int]((Get-Date) - $f.LastWriteTime).TotalSeconds; "
    "Write-Output ('AGE:' + $age); Get-Content $f.FullName -Tail 8 } "
    "else { Write-Output 'AGE:-1' }"
)


def check() -> list[str]:
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                            HOST, PS], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return ["ssh to VPS timed out"]
    if r.returncode != 0:
        return [f"ssh failed: {(r.stderr or '').strip().splitlines()[-1][:160]}"]

    problems, age = [], None
    for line in r.stdout.splitlines():
        if line.startswith("AGE:"):
            age = int(line[4:])
        elif line.lstrip().startswith("{"):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") in BAD_EVENTS:
                problems.append(f"{ev['event']} at {ev.get('ts', '?')}: "
                                f"{ev.get('error', ev.get('equity', ''))}")
    if age is None or age < 0:
        problems.append("no log files on VPS — runner never started?")
    elif age > STALE_SECONDS:
        problems.append(f"log stale for {age}s — runner dead or VPS unreachable")
    return problems


def notify(msg: str) -> None:
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg[:120]}" with title "MT5 watchdog" '
                    'sound name "Basso"'], capture_output=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--loop", type=int, metavar="SECONDS",
                   help="repeat forever at this interval")
    args = p.parse_args()

    while True:
        problems = check()
        stamp = time.strftime("%H:%M:%S")
        if problems:
            for m in problems:
                print(f"[{stamp}] PROBLEM: {m}", flush=True)
            notify(problems[0])
        else:
            print(f"[{stamp}] ok", flush=True)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
