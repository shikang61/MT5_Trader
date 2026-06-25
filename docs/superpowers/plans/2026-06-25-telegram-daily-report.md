# Daily Telegram Performance Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push a per-strategy trading performance summary to Telegram at each weekday gold-session close, run as a VPS scheduled task.

**Architecture:** A thin VPS entrypoint (`scripts/daily_report.py`) gathers broker-true realized P&L from the live MT5 terminal and modeled per-strategy attribution from the local `portfolio.jsonl`, formats a message with a pure builder, and POSTs it to the Telegram Bot API. All formatting and aggregation logic lives in pure, unit-tested functions; the script is the I/O shell.

**Tech Stack:** Python 3.11, `polars` (existing), `python-dotenv` (existing), `urllib.request` (stdlib), `MetaTrader5` (existing, VPS-only), `pytest`/`uv`.

## Global Constraints

- No new third-party dependencies — use `urllib.request` for HTTP (as `scripts/fetch_calendar.py` does) and `python-dotenv` for secrets (as `scripts/run_portfolio.py` does).
- Pure aggregation/formatting functions must unit-test without `MetaTrader5` or network (the `tests/test_broker_pnl.py` pattern: inject dataclass deals / dict steps).
- `MetaTrader5` imports must be lazy (inside the function that needs them) so the script's pure paths import on macOS where the package is absent.
- Package root is `src/mt5_trader/`; tests in `tests/`; run tests with `uv run pytest`.
- Broker magic labels come from `mt5_trader.live.broker_pnl.MAGIC_LABELS` — do not hardcode magic→name elsewhere.
- VPS clock is UTC; the day window is `today 00:00 UTC → now`.

---

### Task 1: Track best/worst realized deal per magic

**Files:**
- Modify: `src/mt5_trader/live/broker_pnl.py`
- Test: `tests/test_broker_pnl.py`

**Interfaces:**
- Consumes: existing `realized_by_magic(deals, entry_out)`.
- Produces: `realized_by_magic` return dict per magic now also has `"best": float` (max realized net deal) and `"worst": float` (min realized net deal), alongside `net/gross/swap/n/wins/symbols`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_broker_pnl.py`:

```python
def test_best_worst_tracked():
    deals = [
        Deal(1001, OUT, profit=20.0, commission=0.0, swap=0.0, symbol="XAUUSD"),   # net +20
        Deal(1001, OUT, profit=-8.0, commission=-1.0, swap=0.0, symbol="XAUUSD"),  # net  -9
        Deal(1001, OUT, profit=5.0, commission=0.0, swap=0.0, symbol="XAUUSD"),    # net  +5
    ]
    out = realized_by_magic(deals, entry_out=OUT)
    assert out[1001]["best"] == 20.0
    assert out[1001]["worst"] == -9.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_broker_pnl.py::test_best_worst_tracked -v`
Expected: FAIL with `KeyError: 'best'`.

- [ ] **Step 3: Write minimal implementation**

In `src/mt5_trader/live/broker_pnl.py`, extend the `defaultdict` factory and the loop body:

```python
    agg: dict[int, dict] = defaultdict(
        lambda: {"net": 0.0, "gross": 0.0, "swap": 0.0, "n": 0, "wins": 0,
                 "best": float("-inf"), "worst": float("inf"), "symbols": set()})
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
        a["best"] = max(a["best"], net_d)
        a["worst"] = min(a["worst"], net_d)
        a["symbols"].add(d.symbol)
    return dict(agg)
```

(Every magic key is created only for an OUT deal, so `best`/`worst` are always overwritten from `±inf` — no empty-key edge case.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_broker_pnl.py -v`
Expected: PASS (all existing tests + `test_best_worst_tracked`).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/broker_pnl.py tests/test_broker_pnl.py
git commit -m "feat(broker_pnl): track best/worst realized deal per magic"
```

---

### Task 2: Track best/worst trade P&L per book in attribution

**Files:**
- Modify: `src/mt5_trader/backtest/attribution.py`
- Test: `tests/test_attribution.py`

**Interfaces:**
- Consumes: existing `attribute(steps)`.
- Produces: `attribute` per-book dict now also has `"best": float | None` and `"worst": float | None` (P&L of the best/worst closed trade run; `None` when the book closed no trades), alongside `gross_pnl/turnover_lots/steps/trades/wins`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_attribution.py` (note the `step()` helper takes `books`; pass `signal` inside each book dict):

```python
def test_best_worst_trade_per_book():
    steps = [
        step(1000, [{"book": "A", "targets": {"X": 1.0}, "signal": 1.0}], {"X": fill(100.0)}),
        step(1010, [{"book": "A", "targets": {"X": 1.0}, "signal": 1.0}], {"X": fill(110.0)}),  # long +10
        step(1010, [{"book": "A", "targets": {"X": 0.0}, "signal": 0.0}], {"X": fill(110.0)}),  # flat -> close +10
        step(1010, [{"book": "A", "targets": {"X": -1.0}, "signal": -1.0}], {"X": fill(110.0)}),# open short
        step(1005, [{"book": "A", "targets": {"X": -1.0}, "signal": -1.0}], {"X": fill(115.0)}),# short -5
        step(1005, [{"book": "A", "targets": {"X": 0.0}, "signal": 0.0}], {"X": fill(115.0)}),  # flat -> close -5
    ]
    r = attribute(steps)
    assert r["A"]["trades"] == 2
    assert r["A"]["wins"] == 1
    assert r["A"]["best"] == 10.0
    assert r["A"]["worst"] == -5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_attribution.py::test_best_worst_trade_per_book -v`
Expected: FAIL with `KeyError: 'best'`.

- [ ] **Step 3: Write minimal implementation**

In `src/mt5_trader/backtest/attribution.py`:

1. Extend the `setdefault` in the holdings-update loop to seed the new keys:

```python
            books.setdefault(name, {"gross_pnl": 0.0, "turnover_lots": 0.0,
                                    "steps": 0, "trades": 0, "wins": 0,
                                    "best": None, "worst": None})
```

2. Extend `close_trade` to record the closed trade's P&L into best/worst:

```python
    def close_trade(name: str) -> None:
        if cur_sign.get(name, 0):
            p = cur_pnl.get(name, 0.0)
            b = books[name]
            b["trades"] += 1
            if p > 0:
                b["wins"] += 1
            if b["best"] is None or p > b["best"]:
                b["best"] = p
            if b["worst"] is None or p < b["worst"]:
                b["worst"] = p
        cur_pnl[name] = 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_attribution.py -v`
Expected: PASS (existing tests still pass — books with no signal runs keep `best/worst = None`).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/backtest/attribution.py tests/test_attribution.py
git commit -m "feat(attribution): track best/worst trade P&L per book"
```

---

### Task 3: Pure report formatter

**Files:**
- Create: `src/mt5_trader/live/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `MAGIC_LABELS` from `broker_pnl`; the `realized_by_magic` dict shape (Task 1); the `attribute` dict shape (Task 2).
- Produces: `build_report(day: str, equity_open: float, equity_now: float, deals: dict, modeled: dict, labels: dict = MAGIC_LABELS) -> str`. `deals` is the `realized_by_magic` output (int-keyed). `modeled` is the `attribute` output (may include an `"_account"` key, which is ignored). Returns the full Telegram message text.

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
from mt5_trader.live.report import build_report

DEALS = {
    1001: {"net": -46.08, "n": 16, "wins": 8, "best": 30.0, "worst": -25.0},
    2003: {"net": 29.44, "n": 7, "wins": 4, "best": 12.0, "worst": -5.0},
}
MODELED = {
    "DonchianBreakout:XAUUSD@M5": {"gross_pnl": 2126.0, "trades": 42, "wins": 26,
                                   "best": 410.0, "worst": -180.0},
    "VwapTrend:XAUUSD@M5": {"gross_pnl": -170.0, "trades": 30, "wins": 12,
                            "best": 60.0, "worst": -90.0},
    "_account": {"first_equity": 99236.0, "last_equity": 100025.0},
}


def test_headline_has_day_equity_and_total():
    msg = build_report("2026-06-25", 99236.0, 100025.0, DEALS, MODELED)
    assert "2026-06-25" in msg
    assert "99,236" in msg and "100,025" in msg     # equity open -> now
    assert "+789.13" in msg                         # equity change
    assert "-16.64" in msg                          # total realized net (sum of deal nets)


def test_realized_section_per_magic_with_winrate():
    msg = build_report("2026-06-25", 99236.0, 100025.0, DEALS, MODELED)
    assert "Portfolio (all books)" in msg           # label for magic 1001
    assert "Passive consolidation" in msg           # label for magic 2003
    assert "50% W" in msg                            # 8/16
    assert "57% W" in msg                            # 4/7 -> round(57.14)


def test_modeled_section_is_labeled_pre_cost_and_sorted():
    msg = build_report("2026-06-25", 99236.0, 100025.0, DEALS, MODELED)
    assert "pre-cost" in msg.lower()                 # disclaimer present
    assert "Donchian" in msg and "Vwap" in msg
    assert msg.index("Donchian") < msg.index("Vwap")  # sorted by gross desc
    assert "best +410.00" in msg and "worst -180.00" in msg


def test_empty_inputs_are_explicit_not_blank():
    msg = build_report("2026-06-25", 0.0, 0.0, {}, {})
    assert "no broker data" in msg.lower()
    assert "no steps today" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mt5_trader.live.report'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/mt5_trader/live/report.py`:

```python
"""Pure formatter for the daily Telegram performance report.

Headline = broker truth (realized net + equity change). Per-strategy section =
modeled gross attribution (pre-cost) reconstructed from the portfolio log — it
will not exactly tie to account equity.
"""
from __future__ import annotations

from mt5_trader.live.broker_pnl import MAGIC_LABELS


def _winpct(wins: int, n: int) -> str:
    return f"{round(wins / n * 100)}% W" if n else "0% W"


def _short(book: str) -> str:
    """'DonchianBreakout:XAUUSD@M5' -> 'DonchianBreakout'."""
    return book.split(":", 1)[0]


def build_report(day: str, equity_open: float, equity_now: float,
                 deals: dict, modeled: dict, labels: dict = MAGIC_LABELS) -> str:
    change = equity_now - equity_open
    lines = [
        f"\U0001F7E1 GOLD DESK — {day} (close)",
        f"Equity  {equity_open:,.0f} → {equity_now:,.0f}   ({change:+,.2f})",
        "",
        "REALIZED today (broker truth, net):",
    ]

    if deals:
        total = 0.0
        for mg in sorted(deals, key=int):
            v = deals[mg]
            total += v["net"]
            label = labels.get(int(mg), str(mg))
            lines.append(
                f"  {label:<22} {v['net']:>+9.2f}  {v['n']:>2} tr  {_winpct(v['wins'], v['n'])}"
                f"  best {v['best']:+.2f}  worst {v['worst']:+.2f}")
        lines.append(f"  ── TOTAL realized {total:>+9.2f}")
    else:
        lines.append("  no broker data")

    lines += ["", "PER-STRATEGY (modeled gross, pre-cost — won't tie to equity):"]
    books = {k: v for k, v in modeled.items() if k != "_account"}
    if books:
        for name, r in sorted(books.items(), key=lambda kv: -kv[1]["gross_pnl"]):
            best = f"best {r['best']:+.2f}" if r["best"] is not None else "best —"
            worst = f"worst {r['worst']:+.2f}" if r["worst"] is not None else "worst —"
            lines.append(
                f"  {_short(name):<12} {r['gross_pnl']:>+9.2f}  {_winpct(r['wins'], r['trades'])}"
                f"  {best}  {worst}  {r['trades']} tr")
    else:
        lines.append("  no steps today")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/report.py tests/test_report.py
git commit -m "feat(report): pure Telegram daily-report formatter"
```

---

### Task 4: Telegram sender

**Files:**
- Create: `src/mt5_trader/live/telegram_send.py`
- Test: `tests/test_telegram_send.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces: `build_request(token: str, chat_id: str, text: str) -> urllib.request.Request` (pure, testable) and `send(token: str, chat_id: str, text: str, timeout: int = 30) -> int` (performs the POST, returns HTTP status).

- [ ] **Step 1: Write the failing test**

Create `tests/test_telegram_send.py`:

```python
import json

from mt5_trader.live.telegram_send import build_request


def test_request_targets_bot_api_with_payload():
    req = build_request("123:ABC", "456", "hello desk")
    assert req.full_url == "https://api.telegram.org/bot123:ABC/sendMessage"
    payload = json.loads(req.data.decode())
    assert payload == {"chat_id": "456", "text": "hello desk"}
    assert req.headers["Content-type"] == "application/json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_telegram_send.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mt5_trader.live.telegram_send'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/mt5_trader/live/telegram_send.py`:

```python
"""Thin Telegram Bot API sender (push only). Stdlib urllib, no new deps."""
from __future__ import annotations

import json
import urllib.request

_API = "https://api.telegram.org/bot{token}/sendMessage"


def build_request(token: str, chat_id: str, text: str) -> urllib.request.Request:
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    return urllib.request.Request(
        _API.format(token=token), data=data,
        headers={"Content-Type": "application/json"})


def send(token: str, chat_id: str, text: str, timeout: int = 30) -> int:
    with urllib.request.urlopen(build_request(token, chat_id, text), timeout=timeout) as r:
        return r.status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_telegram_send.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mt5_trader/live/telegram_send.py tests/test_telegram_send.py
git commit -m "feat(telegram): stdlib Bot API sender"
```

---

### Task 5: VPS entrypoint script

**Files:**
- Create: `scripts/daily_report.py`
- Test: `tests/test_daily_report.py`

**Interfaces:**
- Consumes: `attribute`, `load_steps` (attribution); `realized_by_magic` (broker_pnl); `build_report` (report); `telegram_send.send`.
- Produces: `today_utc() -> str`; `load_today_steps(log: Path, day: str) -> list[dict]` (STEP events whose `ts` date == `day`); a `main()` CLI with `--dry-run`, `--log PATH`, `--no-broker`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_daily_report.py` (tests the pure date-filter; the MT5/Telegram paths are verified by the live dry-run in Step 6):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daily_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.daily_report'` (or `scripts` import error).

If `scripts` is not importable as a package, add an empty `tests/conftest.py` containing `import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))` ONLY if other tests don't already import from `scripts`. Check first: `grep -rl "from scripts" tests/` — if matches exist, the path is already set up and no conftest change is needed.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/daily_report.py`:

```python
#!/usr/bin/env python
"""Daily performance summary to Telegram. Runs ON the VPS at weekday close.

    .venv\\Scripts\\python.exe scripts\\daily_report.py        # gather + send
    uv run scripts/daily_report.py --dry-run                   # print, no send
    uv run scripts/daily_report.py --dry-run --no-broker --log reports/vps_logs/<ts>/portfolio.jsonl
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from mt5_trader.backtest.attribution import attribute, load_steps
from mt5_trader.live import telegram_send
from mt5_trader.live.broker_pnl import realized_by_magic
from mt5_trader.live.report import build_report

DEFAULT_LOG = Path("logs/portfolio.jsonl")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_today_steps(log: Path, day: str) -> list[dict]:
    return [s for s in load_steps(log) if s.get("ts", "")[:10] == day]


def fetch_broker(day: str) -> tuple[dict, float]:
    """(deals_by_magic, equity_now). MetaTrader5 imported lazily (VPS only)."""
    import MetaTrader5 as m

    from mt5_trader.live.mt5_gateway import MT5Gateway

    gw = MT5Gateway()
    gw.connect(login=int(os.environ["MT5_LOGIN"]),
               password=os.environ["MT5_PASSWORD"],
               server=os.environ["MT5_SERVER"])
    frm = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    to = datetime.now(timezone.utc) + timedelta(days=1)
    deals = realized_by_magic(m.history_deals_get(frm, to) or [], m.DEAL_ENTRY_OUT)
    return deals, m.account_info().equity


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="print the message, do not send")
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--no-broker", action="store_true",
                   help="skip MT5 (modeled-only preview; for off-VPS testing)")
    args = p.parse_args()

    load_dotenv()
    day = today_utc()
    steps = load_today_steps(args.log, day) if args.log.exists() else []
    modeled = attribute(steps) if steps else {}
    acct = modeled.get("_account", {})
    equity_open = acct.get("first_equity", 0.0)

    if args.no_broker:
        deals, equity_now = {}, acct.get("last_equity", equity_open)
    else:
        deals, equity_now = fetch_broker(day)

    text = build_report(day, equity_open, equity_now, deals, modeled)

    if args.dry_run:
        print(text)
        return
    telegram_send.send(os.environ["TELEGRAM_BOT_TOKEN"],
                       os.environ["TELEGRAM_CHAT_ID"], text)
    print("sent")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_daily_report.py -v`
Expected: PASS.

- [ ] **Step 5: Local modeled-only smoke (macOS, no MT5)**

Run against the most recent pulled log to eyeball the per-strategy section:

```bash
uv run scripts/daily_report.py --dry-run --no-broker \
  --log "$(ls -t reports/vps_logs/*/portfolio.jsonl | head -1)"
```

Expected: prints a GOLD DESK message; REALIZED section shows "no broker data" (expected off-VPS), PER-STRATEGY section lists books with gross/win%/best/worst. (If today has no steps in that pulled log, it prints "no steps today" — pull a fresh log or accept the empty case.)

- [ ] **Step 6: Commit**

```bash
git add scripts/daily_report.py tests/test_daily_report.py
git commit -m "feat(daily_report): VPS entrypoint + today-slice loader"
```

---

### Task 6: Deploy — secrets, scheduled task, docs

**Files:**
- Modify: `docs/MAC_MINI_SETUP.md` (add the new task to the unattended-tasks list)
- Ops (on the VPS, not in git): `.env`, Windows Task Scheduler

**Interfaces:**
- Consumes: `scripts/daily_report.py` (Task 5).
- Produces: a running `MT5 Daily Report` scheduled task on the VPS firing 21:10 UTC weekdays.

- [ ] **Step 1: Run the full test suite (gate the push)**

Run: `uv run pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 2: Push the branch and pull on the VPS**

```bash
git push -u origin feat/telegram-daily-report
ssh mt5-vps "cd Desktop/MT5_Trader; git fetch; git checkout feat/telegram-daily-report; git pull; .venv\\Scripts\\python.exe -m pytest -q"
```

Expected: tests pass on the VPS.

(If the VPS SSH times out, re-pin first: `bash scripts/provision_azure.sh ip`.)

- [ ] **Step 3: Add Telegram secrets to the VPS `.env`**

User supplies the @BotFather token and numeric chat_id. Append them to the project `.env` on the VPS (same file `load_dotenv()` reads, alongside `MT5_LOGIN`):

```bash
ssh mt5-vps "cd Desktop/MT5_Trader; Add-Content .env 'TELEGRAM_BOT_TOKEN=<token>'; Add-Content .env 'TELEGRAM_CHAT_ID=<chat_id>'"
```

- [ ] **Step 4: Live dry-run on the VPS (real broker data)**

```bash
ssh mt5-vps "cd Desktop/MT5_Trader; .venv\\Scripts\\python.exe scripts\\daily_report.py --dry-run"
```

Expected: prints the full message including the REALIZED (broker truth) section with today's magic-1001 + magic-2003 lines and equity change. Eyeball the numbers against `uv run scripts/pnl_report.py --broker --since <today>`.

- [ ] **Step 5: One real send test**

```bash
ssh mt5-vps "cd Desktop/MT5_Trader; .venv\\Scripts\\python.exe scripts\\daily_report.py"
```

Expected: prints `sent`; the message arrives in the Telegram chat.

- [ ] **Step 6: Create the weekday scheduled task (S4U, mirror the calendar-fetch task)**

```bash
ssh mt5-vps "schtasks /create /tn \"MT5 Daily Report\" /tr \"cmd /c cd /d C:\\Users\\shikang\\Desktop\\MT5_Trader && .venv\\Scripts\\python.exe scripts\\daily_report.py >> logs\\daily_report.log 2>&1\" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 21:10 /ru shikang /rl LIMITED /np /f"
```

Verify it registered: `ssh mt5-vps "schtasks /query /tn \"MT5 Daily Report\" /v /fo LIST | findstr /C:\"Next Run\" /C:\"Schedule\""`

(`/ru shikang /np` = S4U logon, same as the calendar task — runs without storing a password and without an interactive session. If the broker fetch needs the logged-in terminal session and S4U cannot see it, fall back to a logon-context task; confirm via the Step-4 dry-run output that `fetch_broker` returns data under the task's context by running it once via `schtasks /run /tn "MT5 Daily Report"` and checking `logs\daily_report.log`.)

- [ ] **Step 7: Document the task**

In `docs/MAC_MINI_SETUP.md`, in the "Windows (guest)" unattended-tasks list (near the calendar-fetch bullet), add:

```markdown
- Re-create the **weekday daily-report task** (`MT5 Daily Report`, S4U,
  21:10 UTC Mon-Fri): `schtasks /create /tn "MT5 Daily Report" ...`
  (`scripts/daily_report.py` → Telegram). Needs `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_CHAT_ID` in `.env`.
```

- [ ] **Step 8: Commit the doc**

```bash
git add docs/MAC_MINI_SETUP.md
git commit -m "docs(daily_report): record weekday Telegram-report scheduled task"
```

---

## Self-Review

**Spec coverage:**
- Total day P&L → Task 3 (`TOTAL realized`), data from Task 1. ✓
- Per-strategy breakdown (passive + portfolio strategies) → passive via broker magic 2003 (Task 1 + Task 3 realized section); portfolio strategies via modeled attribution (Task 2 + Task 3 per-strategy section). ✓
- Per-strategy win rate → `_winpct` in Task 3 (broker for magics, modeled wins/trades for books). ✓
- Biggest win / biggest loss → `best`/`worst` (Task 1 broker, Task 2 modeled, rendered Task 3). ✓
- Total number of times traded → `n` (broker) / `trades` (modeled) in Task 3. ✓
- Change in equity → Task 3 headline, `equity_now − equity_open` (Task 5 wiring). ✓
- Weekday market-close send → Task 6 Step 6 (`/sc WEEKLY /d MON..FRI /st 21:10`). ✓
- Telegram delivery → Task 4 + Task 5. ✓
- Error handling (no broker data / no steps) → Task 3 `test_empty_inputs_are_explicit_not_blank`. ✓
- Secrets in `.env`, runs on VPS, no new deps → Tasks 4/5/6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The `<token>`/`<chat_id>` in Task 6 are genuine user-supplied secrets, not plan placeholders.

**Type consistency:** `realized_by_magic` dict keys (`net/gross/swap/n/wins/best/worst/symbols`) are produced in Task 1 and consumed by `build_report` in Task 3 (uses `net/n/wins/best/worst`). `attribute` keys (`gross_pnl/trades/wins/best/worst`) produced in Task 2, consumed in Task 3. `build_report` signature in Task 3 matches the call in Task 5. `load_today_steps`/`today_utc`/`fetch_broker` defined and used within Task 5. Consistent.
