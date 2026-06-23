# EURUSD→Gold Lead-Lag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest whether a EURUSD move leads spot gold intraday (tradeable 1–15min lead), as a new cross-asset alpha that trades XAUUSD off a USD signal.

**Architecture:** Combine cross-asset *upstream* (join EURUSD close onto gold M1 bars), exactly like `ratio_mr`'s `spread_bars`. A normal single-series `signal(bars)->np.ndarray` (`UsdLeadLag`) reads the joined `eur_close`; the existing vectorized engine (`backtest/engine.run`) prices gold. Validation is an event-study sweep + null permutation + cost stress.

**Tech Stack:** Python, polars, numpy, the repo's `uv` venv. Spec: `docs/superpowers/specs/2026-06-23-dxy-gold-leadlag-design.md`.

## Global Constraints

- Run everything via `POLARS_SKIP_CPU_CHECK=1 uv run ...` (the venv warns on this CPU).
- Signals decide on bar `t`'s close using data **up to and including t** — never `.shift(-k)` (engine shifts execution +1 bar). See `strategies/base.py`.
- Canonical tick schema = `CORE_COLUMNS = ["ts","symbol","bid","ask","bid_sz","ask_sz"]` (`data/schema.py`); `ts` is `datetime[us, UTC]`.
- Gold's real session: Fri 21:00 → Sun 22:00 UTC closed (`drop_market_closed`, default `fri_close_hour=21`).
- Data dump (in-sample only, external drive): `/Volumes/SK Storage-1/Trading/2026-05-11_2026-06-10` — per-symbol-per-day parquet, vendor cols `time`(str), `sym`, `bid`, `ask`, + L2 lists (unused in v1). Same source/clock for EURUSD and XAUUSD.
- Validation bar (do NOT deploy before all three): param **plateau** (contiguous positive region, not a spike) + **null test p<0.01** + net-positive at **4× real spread**.

---

### Task 1: Ingest EURUSD + XAUUSD from the dump → canonical

**Files:**
- Modify: `config/schema_map.yaml`
- Modify: `src/mt5_trader/data/ingest.py` (add `ingest_parquet_dump`)
- Modify: `scripts/ingest_dump.py` (add `--parquet` + `--symbols`)
- Test: `tests/test_ingest_dump.py`

**Interfaces:**
- Produces: `ingest_parquet_dump(dump_dir, out_dir, symbols, schema_map) -> list[Path]` — reads `{sym}_*.parquet` per symbol, normalises, writes `out_dir/<SYM>/<stem>.parquet`. Canonical output loadable by `load_ticks(out_dir, sym)`.

- [ ] **Step 1: Update the schema map** — `config/schema_map.yaml` `columns:` block becomes exactly:

```yaml
columns:
  time: ts
  sym: symbol
  # bid / ask already match canonical names. L2 list cols (bidprices/bidsizes/
  # askprices/asksizes) are dropped by normalise() (kept set = ALL_COLUMNS).
```

- [ ] **Step 2: Write the failing test** — `tests/test_ingest_dump.py`:

```python
from datetime import datetime, timezone
import polars as pl
from mt5_trader.data.ingest import ingest_parquet_dump, load_ticks, load_schema_map


def test_parquet_dump_to_canonical(tmp_path):
    # a 2-row vendor-format parquet for EURUSD
    raw = pl.DataFrame({
        "time": ["2026-05-11 00:00:00.100000", "2026-05-11 00:00:01.200000"],
        "sym": ["EURUSD", "EURUSD"],
        "bid": [1.1767, 1.1768], "ask": [1.1768, 1.1769],
        "bidprices": [[1.1767, 1.1766], [1.1768, 1.1767]],   # L2 list, must be dropped
        "bidsizes": [[100, 200], [100, 200]],
        "askprices": [[1.1768, 1.1769], [1.1769, 1.1770]],
        "asksizes": [[100, 200], [100, 200]],
    })
    dump = tmp_path / "dump"; dump.mkdir()
    raw.write_parquet(dump / "EURUSD_2026_05_11.parquet")

    out = tmp_path / "processed"
    written = ingest_parquet_dump(dump, out, ["EURUSD"], load_schema_map("config/schema_map.yaml"))
    assert len(written) == 1

    df = load_ticks(out, "EURUSD", drop_weekend=False)
    assert df.columns == ["ts", "symbol", "bid", "ask"]          # L2 lists dropped
    assert df.schema["ts"] == pl.Datetime("us", "UTC")
    assert df["ts"][0] == datetime(2026, 5, 11, 0, 0, 0, 100000, tzinfo=timezone.utc)
    assert abs(df["bid"][0] - 1.1767) < 1e-9
```

- [ ] **Step 3: Run it, verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest tests/test_ingest_dump.py -q`
Expected: FAIL — `ImportError: cannot import name 'ingest_parquet_dump'`.

- [ ] **Step 4: Implement `ingest_parquet_dump`** — add to `src/mt5_trader/data/ingest.py` (next to `ingest_csv_dir`):

```python
def ingest_parquet_dump(dump_dir: Path | str, out_dir: Path | str,
                        symbols: list[str], schema_map: dict[str, str] | None = None,
                        ) -> list[Path]:
    """Per-symbol-per-day parquet dump (cols `time`,`sym`,`bid`,`ask`,L2 lists) ->
    canonical `out_dir/<SYM>/<stem>.parquet`. Reads only the columns we map +
    bid/ask so the 20GB L2 ladder never loads."""
    dump_dir, out_dir = Path(dump_dir), Path(out_dir)
    schema_map = schema_map or {}
    cols = list(schema_map) + ["bid", "ask"]
    written: list[Path] = []
    for sym in symbols:
        for f in sorted(dump_dir.glob(f"{sym}_*.parquet")):
            present = [c for c in cols if c in pl.scan_parquet(f).collect_schema().names()]
            df = normalise(pl.read_parquet(f, columns=present), schema_map)
            dest = out_dir / sym
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"{f.stem}.parquet"
            df.write_parquet(path)
            written.append(path)
    return written
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest tests/test_ingest_dump.py -q`
Expected: PASS.

- [ ] **Step 6: Wire the CLI** — in `scripts/ingest_dump.py`, replace `main()` body to support a parquet dump + symbol subset:

```python
    p.add_argument("csv_dir", type=Path)            # also used as the parquet dump dir
    p.add_argument("--out", type=Path, default=Path("data/processed"))
    p.add_argument("--schema-map", type=Path, default=Path("config/schema_map.yaml"))
    p.add_argument("--parquet", action="store_true", help="dump is per-symbol parquet, not CSV")
    p.add_argument("--symbols", nargs="+", default=["EURUSD", "XAUUSD"])
    args = p.parse_args()
    schema_map = load_schema_map(args.schema_map) if args.schema_map.exists() else None
    if args.parquet:
        from mt5_trader.data.ingest import ingest_parquet_dump
        written = ingest_parquet_dump(args.csv_dir, args.out, args.symbols, schema_map)
    else:
        written = ingest_csv_dir(args.csv_dir, args.out, schema_map)
    print(f"wrote {len(written)} parquet files to {args.out}")
```

- [ ] **Step 7: Run the real ingestion** (external drive must be mounted)

Run:
```bash
POLARS_SKIP_CPU_CHECK=1 uv run scripts/ingest_dump.py \
  "/Volumes/SK Storage-1/Trading/2026-05-11_2026-06-10" \
  --parquet --symbols EURUSD XAUUSD --out data/processed
```
Expected: `wrote 54 parquet files to data/processed` (~27 days × 2 symbols).
Then verify UTC + same clock:
```bash
POLARS_SKIP_CPU_CHECK=1 uv run python -c "from mt5_trader.data.ingest import load_ticks; \
e=load_ticks('data/processed','EURUSD',drop_weekend=False); g=load_ticks('data/processed','XAUUSD',drop_weekend=False); \
print('EUR',e['ts'].min(),e['ts'].max(),e.height); print('XAU',g['ts'].min(),g['ts'].max(),g.height)"
```
Expected: both span ~2026-05-11 → 2026-06-10, ts tz=UTC.

- [ ] **Step 8: Commit**

```bash
git add config/schema_map.yaml src/mt5_trader/data/ingest.py scripts/ingest_dump.py tests/test_ingest_dump.py
git commit -m "feat(data): ingest per-symbol parquet dump (EURUSD+XAUUSD) to canonical"
```

---

### Task 2: `attach_eur` + `UsdLeadLag` signal

**Files:**
- Create: `src/mt5_trader/strategies/usd_leadlag.py`
- Modify: `src/mt5_trader/strategies/__init__.py` (add to the import line)
- Test: `tests/test_usd_leadlag.py`

**Interfaces:**
- Consumes: `strategies/base.register`; gold M1 bars (`ts`,`close`,`high`,`low`,`spread_mean`) + EURUSD M1 bars (`ts`,`close`).
- Produces:
  - `attach_eur(gold_bars: pl.DataFrame, eur_bars: pl.DataFrame) -> pl.DataFrame` — gold bars + `eur_close` column (left join on `ts`, forward-filled).
  - `UsdLeadLag(lead_w:int=3, hold_h:int=10, theta_bp:float=0.0)` with `name="usd_leadlag"`, `.signal(bars)->np.ndarray` in {-1,0,+1}. Requires `bars["eur_close"]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_usd_leadlag.py`:

```python
from datetime import datetime, timedelta, timezone
import numpy as np
import polars as pl
from mt5_trader.strategies.usd_leadlag import UsdLeadLag, attach_eur

T0 = datetime(2026, 5, 12, tzinfo=timezone.utc)
def _bars(closes, col="close"):
    return pl.DataFrame({"ts": [T0 + timedelta(minutes=i) for i in range(len(closes))],
                         col: [float(c) for c in closes]})

def test_attach_eur_joins_and_ffills():
    gold = _bars([4000, 4001, 4002])
    eur = pl.DataFrame({"ts": [T0, T0 + timedelta(minutes=2)], "close": [1.17, 1.18]})
    out = attach_eur(gold, eur)
    assert out["eur_close"].to_list() == [1.17, 1.17, 1.18]   # minute 1 forward-filled

def test_long_when_eur_rises():
    # eur jumps up at bar 3 over lead_w=2 -> gold long for hold_h=2 bars
    eur = [1.0, 1.0, 1.0, 1.02, 1.02, 1.02, 1.02]
    bars = _bars([4000]*7).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    sig = UsdLeadLag(lead_w=2, hold_h=2, theta_bp=0.0).signal(bars)
    assert sig[3] == 1.0 and sig[4] == 1.0   # gold follows eur up
    assert sig[6] == 0.0                      # hold expired

def test_short_when_eur_falls():
    eur = [1.0, 1.0, 1.0, 0.98, 0.98, 0.98, 0.98]
    bars = _bars([4000]*7).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    sig = UsdLeadLag(lead_w=2, hold_h=2, theta_bp=0.0).signal(bars)
    assert sig[3] == -1.0 and sig[4] == -1.0

def test_theta_gate_blocks_small_moves():
    eur = [1.0, 1.0, 1.0, 1.00005, 1.00005, 1.00005]   # ~0.5bp move
    bars = _bars([4000]*6).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    sig = UsdLeadLag(lead_w=2, hold_h=2, theta_bp=5.0).signal(bars)   # 5bp gate
    assert np.all(sig == 0.0)

def test_no_lookahead_prefix_stable():
    eur = [1.0, 1.0, 1.01, 1.01, 1.01, 1.0, 1.0]
    bars = _bars([4000]*7).with_columns(eur_close=pl.Series([float(x) for x in eur]))
    s = UsdLeadLag(lead_w=1, hold_h=2)
    full = s.signal(bars); trunc = s.signal(bars[:4])
    assert np.allclose(full[:4], trunc)
```

- [ ] **Step 2: Run, verify fail**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest tests/test_usd_leadlag.py -q`
Expected: FAIL — `ModuleNotFoundError: mt5_trader.strategies.usd_leadlag`.

- [ ] **Step 3: Implement** — `src/mt5_trader/strategies/usd_leadlag.py`:

```python
"""EURUSD -> gold intraday lead-lag (cross-asset).

USD leads spot gold; gold follows EURUSD (both are inverse-USD, so they co-move).
Combine upstream like meanrev_pairs.spread_bars: join EURUSD onto gold bars, then
a normal single-series signal() reads `eur_close`. M1 bars. See
docs/superpowers/specs/2026-06-23-dxy-gold-leadlag-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .base import register


def attach_eur(gold_bars: pl.DataFrame, eur_bars: pl.DataFrame) -> pl.DataFrame:
    """Gold M1 bars + `eur_close` (left join on ts, forward-filled for any gap).
    Both series must come from the SAME source/clock for the lead-lag to be real."""
    e = eur_bars.select("ts", eur_close=pl.col("close"))
    return (gold_bars.join(e, on="ts", how="left")
            .with_columns(pl.col("eur_close").forward_fill()))


@register
@dataclass
class UsdLeadLag:
    lead_w: int = 3        # bars: EURUSD log-return lookback (the lead window)
    hold_h: int = 10       # bars: hold the gold position after a trigger
    theta_bp: float = 0.0  # |EURUSD move| gate in bp (0 = act on every bar)

    name = "usd_leadlag"

    def signal(self, bars: pl.DataFrame) -> np.ndarray:
        eur = bars["eur_close"].to_numpy().astype(float)
        n = len(eur)
        r = np.full(n, np.nan)
        if n > self.lead_w:
            r[self.lead_w:] = np.log(eur[self.lead_w:] / eur[:-self.lead_w])
        pos = np.zeros(n)
        side, hold = 0.0, 0
        for t in range(n):
            rt = r[t]
            if not np.isnan(rt) and rt != 0.0 and abs(rt) * 1e4 >= self.theta_bp:
                side = float(np.sign(rt))    # gold follows EURUSD
                hold = self.hold_h
            if hold > 0:
                pos[t] = side
                hold -= 1
            else:
                side = 0.0
        return pos
```

- [ ] **Step 4: Register it** — in `src/mt5_trader/strategies/__init__.py`, add `usd_leadlag` to the existing `from . import ...` line so `@register` runs on import.

- [ ] **Step 5: Run tests, verify pass**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run pytest tests/test_usd_leadlag.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/mt5_trader/strategies/usd_leadlag.py src/mt5_trader/strategies/__init__.py tests/test_usd_leadlag.py
git commit -m "feat(strategy): UsdLeadLag EURUSD->gold lead-lag signal + attach_eur"
```

---

### Task 3: Backtest harness (`research_leadlag.py`) + smoke run

**Files:**
- Create: `scripts/research_leadlag.py`

**Interfaces:**
- Consumes: `data/ingest.load_ticks`, `data/bars.time_bars`, `backtest/engine.run`+`BTConfig`, `strategies/usd_leadlag.{UsdLeadLag,attach_eur}`, `research.cost_bp`.
- Produces: `build_bars(data="data/processed") -> pl.DataFrame` (gold M1 + `eur_close`, weekend-filtered, eur non-null); `backtest(strat, bars, slip_bp) -> BTResult`.

- [ ] **Step 1: Write the script skeleton** — `scripts/research_leadlag.py`:

```python
#!/usr/bin/env python
"""EURUSD->gold lead-lag study (M1, same-source dump).

    uv run scripts/research_leadlag.py sweep        # W x H x theta grid (after cost)
    uv run scripts/research_leadlag.py null         # sign-permutation null test
"""
from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from mt5_trader.backtest.engine import BTConfig, run
from mt5_trader.data.bars import time_bars
from mt5_trader.data.ingest import load_ticks
from mt5_trader.research import cost_bp
from mt5_trader.strategies.usd_leadlag import UsdLeadLag, attach_eur

DATA = "data/processed"
KILL = {"London": (7, 10), "NY": (12, 15)}


def build_bars(data: str = DATA) -> pl.DataFrame:
    gold = time_bars(load_ticks(data, "XAUUSD"), "1m")    # load_ticks drops the weekend by default
    eur = time_bars(load_ticks(data, "EURUSD"), "1m")
    return attach_eur(gold, eur).drop_nulls("eur_close")


def backtest(strat, bars: pl.DataFrame, slip_bp: float = 0.5):
    return run(bars, strat.signal(bars), BTConfig(slippage_bps=slip_bp))
```

- [ ] **Step 2: Smoke-run it** (after Task 1 data exists)

Run:
```bash
POLARS_SKIP_CPU_CHECK=1 uv run python -c "import scripts.research_leadlag as r; \
b=r.build_bars(); print('bars', b.height, 'eur_nonnull', b['eur_close'].is_not_null().mean()); \
res=r.backtest(r.UsdLeadLag(lead_w=3,hold_h=10), b); print(res.metrics)"
```
Expected: `bars` ~3e4 (M1 over ~22 trading days), `eur_nonnull` 1.0, and a `metrics` dict with keys like `ann_return`/`sharpe`/`turnover` (whatever `metrics.summary` returns). This proves the end-to-end path; the *value* is not the test — the sweep is.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_leadlag.py
git commit -m "feat(research): lead-lag backtest harness (build_bars + backtest)"
```

---

### Task 4: Parameter sweep (W × H × theta) with cost + session splits

**Files:**
- Modify: `scripts/research_leadlag.py` (add `sweep` subcommand)

**Interfaces:**
- Consumes: Task 3's `build_bars`, `backtest`.
- Produces: a printed grid of after-cost return/Sharpe/turnover per (W, H, theta), plus London/NY kill-zone and weekly net-return splits.

- [ ] **Step 1: Add the sweep** — append to `scripts/research_leadlag.py`:

```python
W_GRID = [1, 2, 3, 5, 8, 13]
H_GRID = [1, 3, 5, 10, 20, 30]
THETA_GRID = [0.0, 0.5, 1.0]      # bp gate


def _net_bp(bars: pl.DataFrame, strat, slip_bp: float) -> tuple[float, float, float]:
    res = backtest(strat, bars, slip_bp)
    m = res.metrics
    return m.get("ann_return", float("nan")), m.get("sharpe", float("nan")), m.get("turnover", float("nan"))


def sweep(bars: pl.DataFrame) -> None:
    hurdle = cost_bp(bars, slip_bp=0.5)
    print(f"per-trade cost hurdle ~{hurdle:.3f} bp; grid = after 1x cost (slip 0.5bp)\n")
    print(f"{'W':>3}{'H':>4}{'theta':>7}{'annRet':>9}{'Sharpe':>8}{'turn':>8}")
    best = None
    for w in W_GRID:
        for h in H_GRID:
            for th in THETA_GRID:
                ann, sh, tn = _net_bp(bars, UsdLeadLag(lead_w=w, hold_h=h, theta_bp=th), 0.5)
                print(f"{w:>3}{h:>4}{th:>7.1f}{ann:>9.4f}{sh:>8.2f}{tn:>8.1f}")
                if best is None or (sh == sh and sh > best[0]):
                    best = (sh, w, h, th)
    print(f"\nbest Sharpe: {best}")
    print("\n--- look for a CONTIGUOUS positive-Sharpe region (plateau), not a lone spike ---")
    _session_splits(bars, UsdLeadLag(lead_w=best[1], hold_h=best[2], theta_bp=best[3]))


def _session_splits(bars: pl.DataFrame, strat) -> None:
    sig = strat.signal(bars)
    close = bars["close"].to_numpy()
    ret = np.zeros_like(close); ret[:-1] = np.diff(close) / close[:-1]
    pnl = sig * np.concatenate([ret[1:], [0.0]])     # position t earns t->t+1 return
    hour = bars["ts"].dt.hour().to_numpy()
    week = bars["ts"].dt.week().to_numpy()
    print("\n-- kill-zone net (best params, gross) --")
    for name, (lo, hi) in KILL.items():
        m = (hour >= lo) & (hour < hi)
        print(f"  {name:7} sum={pnl[m].sum()*1e4:>8.1f}bp n={int(m.sum())}")
    print("-- weekly net (gross) --")
    for wk in sorted(set(week.tolist())):
        m = week == wk
        print(f"  week {wk}: {pnl[m].sum()*1e4:>8.1f}bp")
```

And extend `main()` at the bottom:

```python
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sweep", "null"])
    args = ap.parse_args()
    bars = build_bars()
    print(f"bars={bars.height}  span={bars['ts'].min()} -> {bars['ts'].max()}\n")
    if args.cmd == "sweep":
        sweep(bars)
    elif args.cmd == "null":
        null_test(bars)        # Task 5


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the sweep**

Run: `POLARS_SKIP_CPU_CHECK=1 uv run scripts/research_leadlag.py sweep`
Expected: a full grid table + best-Sharpe params + kill-zone/weekly splits. **Verification (judgment, not assert):** is there a *contiguous* positive-Sharpe region across neighbouring W/H (a plateau)? If only isolated cells are positive → no robust lead → likely a negative result (document + stop before deploy).

- [ ] **Step 3: Commit**

```bash
git add scripts/research_leadlag.py
git commit -m "feat(research): lead-lag W x H x theta sweep + session/weekly splits"
```

---

### Task 5: Null sign-permutation test + 4× cost stress

**Files:**
- Modify: `scripts/research_leadlag.py` (add `null_test`)

**Interfaces:**
- Consumes: `build_bars`, `backtest`, the best params printed by Task 4.
- Produces: p-value = P(a circularly-shifted EURUSD beats the real-aligned signal), and net return at 1× vs 4× cost.

- [ ] **Step 1: Add the null test** — append to `scripts/research_leadlag.py` (mirrors `research_reopen_fade.py`'s null permutation):

```python
BEST = dict(lead_w=3, hold_h=10, theta_bp=0.0)   # EDIT to the plateau centre from Task 4


def _ann_return(bars, strat, slip_bp):
    return backtest(strat, bars, slip_bp).metrics.get("ann_return", float("nan"))


def null_test(bars: pl.DataFrame, n_perm: int = 200, seed: int = 0) -> None:
    strat = UsdLeadLag(**BEST)
    actual = _ann_return(bars, strat, 0.5)
    print(f"actual annRet (1x cost) = {actual:.4f}  params={BEST}")

    rng = np.random.default_rng(seed)
    eur = bars["eur_close"].to_numpy()
    n = len(eur)
    beat = 0
    null = np.empty(n_perm)
    for i in range(n_perm):
        shift = int(rng.integers(n // 10, n - n // 10))    # circular shift breaks the lead
        shuffled = bars.with_columns(eur_close=pl.Series(np.roll(eur, shift)))
        null[i] = _ann_return(shuffled, strat, 0.5)
        beat += null[i] >= actual
    p = (beat + 1) / (n_perm + 1)
    print(f"null mean={null.mean():.4f} std={null.std():.4f}  p(null>=actual)={p:.4f}  "
          f"-> {'PASS' if p < 0.01 else 'FAIL'} (<0.01)")

    print("\n-- cost stress --")
    for k, slip in ((1, 0.5), (4, 2.0)):
        print(f"  {k}x cost (slip {slip}bp): annRet={_ann_return(bars, strat, slip):.4f}")
```

- [ ] **Step 2: Run the null + cost test** (after setting `BEST` to Task 4's plateau centre)

Run: `POLARS_SKIP_CPU_CHECK=1 uv run scripts/research_leadlag.py null`
Expected: prints actual return, null distribution, `p(null>=actual)`, and 1×/4× cost returns. **Verdict:** the strategy earns a portfolio slot ONLY if `p < 0.01` AND 4× cost return > 0 AND Task 4 showed a plateau. Otherwise document and shelve (do not deploy) — like the dead HF sweep.

- [ ] **Step 3: Commit**

```bash
git add scripts/research_leadlag.py
git commit -m "feat(research): lead-lag null permutation + 4x cost stress"
```

---

## Self-review notes

- **Spec coverage:** signal (Task 2), same-source ingestion (Task 1), `ratio_mr`-style upstream join (Task 2 `attach_eur`), plateau+null+4×cost validation (Tasks 4–5), backtest-only (no portfolio wiring in scope). OOS/live capture and ②/③ are spec "out of scope" — not in this plan, correct.
- **Types:** `attach_eur`/`UsdLeadLag.signal`/`build_bars`/`backtest` signatures are consistent across tasks; `metrics` keys (`ann_return`,`sharpe`,`turnover`) are read defensively with `.get(...)` since `metrics.summary`'s exact keys should be confirmed at Task 3 smoke (adjust the printed key names there if they differ — a 1-line fix, flagged).
- **Deferred decision:** `BEST` in Task 5 is edited to the Task 4 plateau centre (not a placeholder — an explicit hand-off, the sweep must run first).
