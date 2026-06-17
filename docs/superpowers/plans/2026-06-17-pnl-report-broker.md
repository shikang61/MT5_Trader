# pnl_report Broker P&L by Magic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pnl_report --broker` showing realized P&L per magic from the broker (incl. the consolidation passive book, magic 2003), reusing `remote_probe`'s deals query via a shared, tested aggregation.

**Architecture:** Extract `remote_probe`'s deals-by-magic loop into a pure `broker_pnl.realized_by_magic` (unit-tested without MetaTrader5). `remote_probe` is refactored onto it (output unchanged). `pnl_report --broker` SSHes the VPS, runs `remote_probe`, and prints a labeled per-magic table from its `deals` field. Offline default report is untouched.

**Tech Stack:** Python 3.11; pytest; SSH to `mt5-vps`; MetaTrader5 (VPS-only, already guarded).

---

## Task 1: `broker_pnl.realized_by_magic` + test

**Files:**
- Create: `src/mt5_trader/live/broker_pnl.py`
- Test: `tests/test_broker_pnl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_broker_pnl.py
from dataclasses import dataclass

from mt5_trader.live.broker_pnl import MAGIC_LABELS, realized_by_magic

OUT = 1   # sentinel for DEAL_ENTRY_OUT; IN deals use a different value


@dataclass
class Deal:
    magic: int
    entry: int
    profit: float
    commission: float
    swap: float
    symbol: str


def test_only_closing_deals_counted():
    deals = [
        Deal(2003, entry=0, profit=99.0, commission=0.0, swap=0.0, symbol="XAGUSD"),  # IN -> ignored
        Deal(2003, entry=OUT, profit=10.0, commission=-1.0, swap=-0.5, symbol="XAGUSD"),
    ]
    out = realized_by_magic(deals, entry_out=OUT)
    assert out[2003]["n"] == 1
    assert abs(out[2003]["net"] - (10.0 - 1.0 - 0.5)) < 1e-9   # profit+commission+swap
    assert abs(out[2003]["gross"] - 10.0) < 1e-9
    assert abs(out[2003]["swap"] - (-0.5)) < 1e-9
    assert out[2003]["wins"] == 1


def test_grouping_and_wins():
    deals = [
        Deal(1001, entry=OUT, profit=5.0, commission=0.0, swap=0.0, symbol="XAUUSD"),
        Deal(1001, entry=OUT, profit=-8.0, commission=0.0, swap=0.0, symbol="XAUUSD"),
        Deal(2003, entry=OUT, profit=2.0, commission=0.0, swap=0.0, symbol="XAGUSD"),
    ]
    out = realized_by_magic(deals, entry_out=OUT)
    assert out[1001]["n"] == 2 and out[1001]["wins"] == 1
    assert abs(out[1001]["net"] - (-3.0)) < 1e-9
    assert out[2003]["n"] == 1 and out[2003]["symbols"] == {"XAGUSD"}


def test_empty():
    assert realized_by_magic([], entry_out=OUT) == {}


def test_labels_present():
    assert MAGIC_LABELS[1001] and MAGIC_LABELS[2003]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_broker_pnl.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mt5_trader.live.broker_pnl'`

- [ ] **Step 3: Write the module**

```python
# src/mt5_trader/live/broker_pnl.py
"""Realized P&L by magic from broker deal history (broker truth).

Shared by dashboard/remote_probe.py and scripts/pnl_report.py --broker. The
aggregation is a pure function so it unit-tests without MetaTrader5."""
from __future__ import annotations

from collections import defaultdict

MAGIC_LABELS = {
    1001: "portfolio (all books)",
    2002: "pdhl passive (retired)",
    2003: "consolidation passive",
}


def realized_by_magic(deals, entry_out) -> dict[int, dict]:
    """Aggregate CLOSED broker deals by magic. Only deals with `d.entry == entry_out`
    (DEAL_ENTRY_OUT) carry realized P&L. Returns
    {magic: {"net","gross","swap","n","wins","symbols"}} where
    net = profit + commission + swap, gross = profit, symbols is a set."""
    agg: dict[int, dict] = defaultdict(
        lambda: {"net": 0.0, "gross": 0.0, "swap": 0.0, "n": 0, "wins": 0, "symbols": set()})
    for d in deals:
        if d.entry != entry_out:
            continue
        net_d = d.profit + d.commission + d.swap
        a = agg[d.magic]
        a["net"] += net_d
        a["gross"] += d.profit
        a["swap"] += d.swap
        a["n"] += 1
        a["wins"] += 1 if net_d > 0 else 0
        a["symbols"].add(d.symbol)
    return dict(agg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_broker_pnl.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/broker_pnl.py tests/test_broker_pnl.py
git commit -m "feat(broker_pnl): pure realized-P&L-by-magic aggregation + tests"
```

---

## Task 2: Refactor `remote_probe.py` onto the shared function

**Files:**
- Modify: `dashboard/remote_probe.py`

The current inline loop produces `deals = {str(magic): {net,gross,swap,n,wins,symbols(sorted list)}}`.
Preserve that output exactly.

- [ ] **Step 1: Replace the inline aggregation**

In `dashboard/remote_probe.py`, add the import near the top with the others:

```python
from mt5_trader.live.broker_pnl import realized_by_magic
```

Replace this block:

```python
    agg: dict[int, dict] = defaultdict(
        lambda: {"net": 0.0, "gross": 0.0, "swap": 0.0, "n": 0, "wins": 0, "symbols": set()})
    for d in (m.history_deals_get(frm, to) or []):
        if d.entry != m.DEAL_ENTRY_OUT:        # only closing deals carry realized P&L
            continue
        net_d = d.profit + d.commission + d.swap
        a = agg[d.magic]
        a["net"] += net_d
        a["gross"] += d.profit
        a["swap"] += d.swap
        a["n"] += 1
        a["wins"] += 1 if net_d > 0 else 0
        a["symbols"].add(d.symbol)
    deals = {str(k): {**v, "symbols": sorted(v["symbols"])} for k, v in agg.items()}
```

with:

```python
    agg = realized_by_magic(m.history_deals_get(frm, to) or [], m.DEAL_ENTRY_OUT)
    deals = {str(k): {**v, "symbols": sorted(v["symbols"])} for k, v in agg.items()}
```

Then remove the now-unused `from collections import defaultdict` import if nothing
else uses it (check the file — `agg`/`defaultdict` was its only use).

- [ ] **Step 2: Syntax + import check (MT5 path can't run locally)**

Run: `uv run python -c "import ast; ast.parse(open('dashboard/remote_probe.py').read()); print('OK syntax')"`
Expected: `OK syntax`.
Note: `remote_probe` imports MetaTrader5 at module top, so it can't execute on the Mac;
the aggregation it now calls is covered by `tests/test_broker_pnl.py`. Output shape is
unchanged (same `deals` dict construction), so the dashboard keeps working.

- [ ] **Step 3: Commit**

```bash
git add dashboard/remote_probe.py
git commit -m "refactor(remote_probe): use shared broker_pnl.realized_by_magic"
```

---

## Task 3: `pnl_report --broker`

**Files:**
- Modify: `scripts/pnl_report.py`

- [ ] **Step 1: Add the broker fetch + table printer + flag**

Replace the body of `scripts/pnl_report.py` with (keeps the offline default intact,
adds `--broker`/`--since`):

```python
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
    ps = f"cd {REPO}; .venv\\Scripts\\python.exe dashboard/remote_probe.py {since}"
    try:
        out = subprocess.run(["ssh", HOST, "powershell", "-NoProfile", "-Command", ps],
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
```

- [ ] **Step 2: Offline path still works (no regression)**

Run: `uv run scripts/pnl_report.py`
Expected: the usual per-strategy attribution table (unchanged), no error.

- [ ] **Step 3: Live broker smoke-run**

Run: `uv run scripts/pnl_report.py --broker`
Expected: a per-magic table; row `2003  consolidation passive  <net> ...` present
(and `1001 portfolio (all books)`). No traceback. (Needs SSH to `mt5-vps` reachable —
NSG pinned to the current IP.)

- [ ] **Step 4: Full suite**

Run: `uv run pytest -q`
Expected: PASS (all green; +4 from test_broker_pnl).

- [ ] **Step 5: Commit**

```bash
git add scripts/pnl_report.py
git commit -m "feat(pnl_report): --broker realized P&L by magic incl passive book"
```

---

## Self-review notes

- **Spec coverage:** pure `realized_by_magic` + labels (Task 1, tested); remote_probe
  refactor preserving output (Task 2); `pnl_report --broker` labeled table via SSH
  (Task 3); offline default untouched (Task 3 keeps the original branch). All covered.
- **Output preservation:** Task 2 keeps the exact `deals = {str(k): {**v, "symbols":
  sorted(...)}}` construction, so the dashboard's consumer sees no change. `realized_by_magic`
  returns `symbols` as a set (remote_probe sorts it) — matches the original.
- **Testability:** `realized_by_magic` takes `entry_out` as a param so the test passes a
  sentinel and plain `Deal` stubs — no MetaTrader5 needed. remote_probe passes
  `m.DEAL_ENTRY_OUT`.
- **Granularity:** broker groups by magic → portfolio is one line (1001), passive its own
  (2003); per-strategy split stays in the offline report.
