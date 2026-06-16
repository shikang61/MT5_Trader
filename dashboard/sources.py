"""Mac-side data access for the live dashboard.

Two pulls per refresh:
  * probe()        -> one SSH call runs dashboard/remote_probe.py on the VPS and
                      returns broker truth (account, positions, deals-by-magic) +
                      the same bars the bot signals on, as a dict.
  * pull_log()     -> scp the portfolio.jsonl decision log to a local cache, for
                      the equity curve, per-book attribution and fill-quality.

Everything here is read-only over SSH. If SSH times out the NSG pin probably
went stale (home IP changed) -- run: bash scripts/provision_azure.sh ip
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import polars as pl

from mt5_trader.backtest.attribution import attribute, load_steps
from mt5_trader.features.regime import RANGE, TREND_DOWN, TREND_UP, regime_series
from mt5_trader.live.runner import Book
from mt5_trader.strategies import REGISTRY
from mt5_trader.strategies.meanrev_pairs import spread_bars

HOST = "mt5-vps"
REMOTE = "Desktop/MT5_Trader"
CACHE = Path("dashboard/.cache")


class SSHError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 40) -> str:
    """Run an ssh/scp command, return stdout. SSH warnings go to stderr so the
    JSON on stdout stays clean."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SSHError(f"timeout — NSG pin may be stale (scripts/provision_azure.sh ip): {e}")
    if p.returncode != 0:
        raise SSHError(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "ssh failed")
    return p.stdout


def probe(since: str = "2026-06-12") -> dict:
    """Refresh the VPS probe and return its JSON (account/positions/deals/bars)."""
    _run(["scp", "-q", "dashboard/remote_probe.py", f"{HOST}:{REMOTE}/dashboard/remote_probe.py"])
    out = _run(["ssh", HOST,
                f"cd {REMOTE}; .venv\\Scripts\\python.exe dashboard\\remote_probe.py {since}"])
    return json.loads(out)


def pull_log(name: str = "portfolio.jsonl") -> Path:
    """scp a decision log to the local cache; returns the local path."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / name
    _run(["scp", "-q", f"{HOST}:{REMOTE}/logs/{name}", str(dest)], timeout=60)
    return dest


# ---- parsing the probe bars + log into frames the panels render --------------

def bars_frame(probe_bars: dict, key: str) -> pl.DataFrame:
    b = probe_bars[key]
    cols = {c: b[c] for c in ("open", "high", "low", "close", "spread_mean") if c in b}
    return pl.DataFrame({
        "ts": pl.Series(b["ts"]).str.to_datetime(time_unit="us", time_zone="UTC"),
        **cols,
    }).sort("ts")


def equity_curve(log_path: Path) -> pl.DataFrame:
    rows = [(ev["ts"], ev["equity"]) for ev in _steps(log_path)]
    return pl.DataFrame({"ts": [r[0] for r in rows], "equity": [r[1] for r in rows]}) \
        .with_columns(pl.col("ts").str.to_datetime(time_unit="us", time_zone="UTC"))


def attribution_table(log_path: Path) -> pl.DataFrame:
    res = attribute(load_steps(log_path))
    res.pop("_account", None)
    return pl.DataFrame([
        {"book": k, "gross_pnl": v["gross_pnl"], "turnover": v["turnover_lots"],
         "trades": v.get("trades", 0),
         "win%": round(100 * v.get("wins", 0) / v["trades"]) if v.get("trades") else 0,
         "steps": v["steps"]} for k, v in res.items()
    ]).sort("gross_pnl", descending=True)


def latest_signals(log_path: Path) -> pl.DataFrame:
    steps = list(_steps(log_path))
    if not steps:
        return pl.DataFrame()
    last = steps[-1]
    return pl.DataFrame([
        {"book": b["book"], "bar_ts": b["bar_ts"], "signal": b["signal"],
         "target": next(iter(b.get("targets", {}).values()), 0.0)}
        for b in last.get("books", [])
    ])


def deals_table(probe_data: dict) -> pl.DataFrame:
    name = {"0": "untagged", "1001": "portfolio", "2002": "pdhl", "2003": "consolidation"}
    rows = [{"magic": name.get(k, k), "net": v["net"], "gross": v["gross"],
             "swap": v["swap"], "closes": v["n"],
             "win%": round(100 * v.get("wins", 0) / v["n"]) if v["n"] else 0,
             "symbols": ",".join(v["symbols"])}
            for k, v in probe_data["deals"].items()]
    return pl.DataFrame(rows).sort("net") if rows else pl.DataFrame()


def regime_state(probe_bars: dict) -> str:
    """Current regime of the XAU/XAG ratio spread (what ratio_mr gates on)."""
    xau, xag = bars_frame(probe_bars, "XAUUSD@M5"), bars_frame(probe_bars, "XAGUSD@M5")
    spread = spread_bars(xau, xag, beta=1.0)
    reg = regime_series(spread)[-1]
    return {TREND_UP: "TREND_UP", RANGE: "RANGE", TREND_DOWN: "TREND_DOWN"}[int(reg)]


def fill_quality(log_path: Path, probe_bars: dict,
                 cfg_path: Path = Path("config/portfolio.yaml")) -> pl.DataFrame:
    """Per book: does the live decision match the completed-bar backtest signal?
    After the forming-bar fix this should sit near 100%."""
    import yaml
    cfg = yaml.safe_load(open(cfg_path))
    live: dict[str, dict[str, float]] = {}
    for ev in _steps(log_path):
        for b in ev.get("books", []):
            live.setdefault(b["book"], {})[b["bar_ts"]] = b["signal"]

    rows = []
    for spec in cfg["books"]:
        book = Book(strategy=REGISTRY[spec["strategy"]](**spec.get("params", {})),
                    symbol=spec["symbol"], symbol_b=spec.get("symbol2", ""),
                    beta=float(spec.get("beta", 1.0)),
                    trade_symbol_b=bool(spec.get("trade_symbol2", True)),
                    timeframe=spec.get("timeframe", "M1"))
        name = book._name()
        if name not in live:
            continue
        key = f"{spec['symbol']}@{spec['timeframe']}"
        if key not in probe_bars:
            continue
        bars = bars_frame(probe_bars, spec["symbol"] + "@" + spec["timeframe"])
        if spec.get("symbol2"):
            kb = f"{spec['symbol2']}@{spec['timeframe']}"
            if kb not in probe_bars:
                continue
            bars = spread_bars(bars, bars_frame(probe_bars, kb), float(spec.get("beta", 1.0)))
        closed = bars[:-1]                       # completed-bar convention (matches the bot)
        try:
            sig = book.strategy.signal(closed)
        except Exception:
            continue                             # strategy needs a column the probe omits
        bt = {t.strftime("%Y-%m-%d %H:%M:%S+00:00"): s for t, s in zip(closed["ts"], sig)}
        common = [k for k in live[name] if k in bt]
        if not common:
            continue
        agree = sum(1 for k in common if abs(live[name][k] - bt[k]) < 1e-9)
        rows.append({"book": name, "agree_pct": round(100 * agree / len(common), 1),
                     "matched_bars": len(common),
                     "live_active%": round(100 * sum(live[name][k] != 0 for k in common) / len(common), 1),
                     "bt_active%": round(100 * sum(bt[k] != 0 for k in common) / len(common), 1)})
    return pl.DataFrame(rows)


def _steps(log_path: Path):
    with open(log_path) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "STEP":
                yield ev
