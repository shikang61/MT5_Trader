#!/usr/bin/env python
"""Per-strategy P&L attribution from a portfolio log, plus broker realized P&L
by magic.

    bash scripts/fetch_logs.sh                  # pull fresh logs first
    uv run scripts/pnl_report.py                # offline per-strategy attribution
    uv run scripts/pnl_report.py path/to/portfolio.jsonl
    uv run scripts/pnl_report.py --broker       # broker realized P&L by magic (live, via SSH)
    uv run scripts/pnl_report.py --broker --since 2026-06-15
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from mt5_trader.backtest.attribution import attribute, format_report, load_steps
from mt5_trader.live.broker_pnl import MAGIC_LABELS

HOST = "mt5-vps"
REPO = r"C:\Users\shikang\Desktop\MT5_Trader"


def newest_pulled_log() -> Path:
    candidates = sorted(Path("reports/vps_logs").glob("*/portfolio.jsonl"))
    if not candidates:
        sys.exit("no pulled portfolio.jsonl found — run: bash scripts/fetch_logs.sh")
    return candidates[-1]


def fetch_broker_deals(since: str) -> dict:
    """Run remote_probe on the VPS over SSH and return its per-magic deals dict."""
    # remote default shell is PowerShell; pass the whole command as one ssh arg.
    remote = f"cd {REPO}; .venv\\Scripts\\python.exe dashboard/remote_probe.py {since}"
    try:
        out = subprocess.run(["ssh", HOST, remote],
                             capture_output=True, text=True, timeout=120, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        sys.exit(f"broker fetch failed (ssh {HOST}): {e}")
    line = next((ln for ln in out.splitlines() if ln.startswith("{")), "")
    if not line:
        sys.exit(f"no JSON from remote_probe; got:\n{out[-500:]}")
    return json.loads(line)["deals"]


def print_broker(deals: dict) -> None:
    print(f"\n{'magic':>6}  {'label':<24}{'net':>10}{'gross':>10}{'swap':>9}{'n':>5}{'win%':>6}")
    for mg in sorted(deals, key=int):
        v = deals[mg]
        win = (v["wins"] / v["n"] * 100) if v["n"] else 0.0
        label = MAGIC_LABELS.get(int(mg), str(mg))
        print(f"{mg:>6}  {label:<24}{v['net']:>+10.2f}{v['gross']:>+10.2f}"
              f"{v['swap']:>+9.2f}{v['n']:>5}{win:>5.0f}%")
    tot = sum(v["net"] for v in deals.values())
    print(f"{'':>6}  {'TOTAL realized (net)':<24}{tot:>+10.2f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("log", nargs="?", type=Path, default=None)
    p.add_argument("--broker", action="store_true",
                   help="broker realized P&L by magic (live, via SSH to the VPS)")
    p.add_argument("--since", default="2026-06-12", help="broker deal window start (YYYY-MM-DD)")
    args = p.parse_args()

    if args.broker:
        print(f"broker realized P&L by magic (since {args.since}):")
        print_broker(fetch_broker_deals(args.since))
        return

    log = args.log or newest_pulled_log()
    steps = load_steps(log)
    if not steps:
        sys.exit(f"no STEP events with book data in {log}")
    print(f"source: {log}\n")
    print(format_report(attribute(steps)))


if __name__ == "__main__":
    main()
