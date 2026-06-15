#!/usr/bin/env python
"""Strategy development pipeline CLI: discovery → backtest/validate → deploy,
with the stash as the holding pen for sub-par candidates worth iterating on.

    uv run scripts/strategy.py list                       # the pipeline board
    uv run scripts/strategy.py diagnose donchian          # Stage-2 signal stats
    uv run scripts/strategy.py validate donchian --every 5m
    uv run scripts/strategy.py stash add regime_switch --improve "try wider exit"
    uv run scripts/strategy.py stash list
    uv run scripts/strategy.py promote sweep_fade --weight 0.1   # gated on validate
    uv run scripts/strategy.py new my_idea

Runs on real ticks (data/processed) when present, else deterministic synthetic
bars so the pipeline works before the dump lands (results are illustrative then).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from mt5_trader.pipeline import diagnostics, stash, trials, validate
from mt5_trader.pipeline.data import load_or_synthetic
from mt5_trader.strategies import REGISTRY

PORTFOLIO = Path("config/portfolio.yaml")
VERDICTS = Path("research/verdicts.json")
STRAT_DIR = Path("src/mt5_trader/strategies")

# primary param to wiggle +/-25% per strategy (omitted = wiggle gate skipped)
WIGGLE = {
    "donchian": ("entry_n", [180, 240, 300]),
    "vwap_trend": ("z_n", [180, 240, 300]),
    "london_orb": ("flat_hour", [14, 16, 18]),
    "sweep_fade": ("quiet_bars", [45, 60, 75]),
    "ratio_mr": ("z_n", [216, 288, 360]),
    "regime_switch": ("range_n", [180, 240, 300]),
    "fast_mr": ("z_n", [45, 60, 75]),
}


# ---- small persistence helpers -------------------------------------------------

def _deployed() -> dict[str, dict]:
    if not PORTFOLIO.exists():
        return {}
    cfg = yaml.safe_load(PORTFOLIO.read_text()) or {}
    return {b["strategy"]: b for b in cfg.get("books", [])}


def _load_verdicts() -> dict:
    return json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}


def _save_verdict(name: str, record: dict) -> None:
    v = _load_verdicts()
    v[name] = record
    VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    VERDICTS.write_text(json.dumps(v, indent=2, default=str))


def _parse_set(pairs: list[str] | None) -> dict:
    out: dict = {}
    for p in pairs or []:
        k, v = p.split("=", 1)
        for cast in (int, float):
            try:
                out[k] = cast(v)
                break
            except ValueError:
                continue
        else:
            out[k] = v
    return out


def _bars(args):
    bars, is_real = load_or_synthetic(args.symbol, args.every)
    if not is_real:
        print(f"  (no data/processed/{args.symbol} — using SYNTHETIC bars; "
              f"results illustrative, not tradeable)\n")
    return bars


# ---- commands ------------------------------------------------------------------

def cmd_list(args):
    deployed, stashed = _deployed(), stash.list_()
    print(f"{'strategy':<16}{'status':<12}{'where':<28}")
    print("-" * 56)
    for name in sorted(REGISTRY):
        if name in deployed:
            st, where = "deployed", f"weight={deployed[name].get('weight', 1)}"
        elif name in stashed:
            st, where = "stashed", ",".join(stashed[name].get("failed_gates", [])) or "needs work"
        else:
            st, where = "candidate", "not deployed/stashed"
        print(f"{name:<16}{st:<12}{where:<28}")
    extra = [n for n in stashed if n not in REGISTRY]
    for name in sorted(extra):  # stashed but unregistered (e.g. parked harnesses)
        print(f"{name:<16}{'stashed':<12}{'(unregistered) ' + (','.join(stashed[name].get('failed_gates', [])))}")


def cmd_diagnose(args):
    bars = _bars(args)
    sig = REGISTRY[args.name]().signal(bars)
    rep = diagnostics.diagnose(bars, sig)
    print(f"=== diagnose {args.name} @ {args.every} ===")
    print(f"  IC {rep['ic']:+.4f}  rankIC {rep['rank_ic']:+.4f}  "
          f"turnover {rep['turnover']:.0f}  active {rep['active_frac']:.2f}")
    print(f"  half-life: {rep['decay']['half_life']} bars   "
          f"IC by horizon: " + " ".join(f"{h}:{v:+.3f}" for h, v in rep['decay']['ic_by_horizon'].items()))
    print("  by regime: " + "  ".join(
        f"{k} IC{d['ic']:+.3f}(n={d['n']})" for k, d in rep['by_regime'].items()))
    trials.log_trial(f"diagnose {args.name}", verdict="diagnose")


def cmd_validate(args):
    bars = _bars(args)
    params = _parse_set(args.set)
    verdict = validate.validate_candidate(
        args.name, bars, args.every, wiggle=WIGGLE.get(args.name),
        n_trials=max(trials.count(), 1), **params)
    print(f"=== validate {args.name} @ {args.every}  ->  "
          f"{'PASS' if verdict['overall'] else 'FAIL'} ===")
    for gate, d in verdict["gates"].items():
        flag = "ok " if d["pass"] else "XX "
        detail = " ".join(f"{k}={v}" for k, v in d.items() if k != "pass")
        print(f"  [{flag}] {gate:<16} {detail}")
    trials.log_trial(f"validate {args.name}", params=params,
                     verdict="PASS" if verdict["overall"] else "FAIL")
    failed = [g for g, d in verdict["gates"].items() if not d["pass"]]
    _save_verdict(args.name, {
        "overall": verdict["overall"], "every": args.every, "params": params,
        "failed_gates": failed, "metrics": verdict["metrics"],
        "ts": datetime.now(timezone.utc).isoformat()})
    if verdict["overall"]:
        print(f"\n  → PASS. Deploy with: strategy.py promote {args.name} --weight <w>")
    else:
        print(f"\n  → FAIL ({', '.join(failed)}). Stash with: "
              f"strategy.py stash add {args.name} --improve \"...\"")


def cmd_stash(args):
    if args.action == "list":
        s = stash.list_()
        if not s:
            print("stash empty")
            return
        for name, e in sorted(s.items()):
            print(f"{name:<16} gates={','.join(e.get('failed_gates', [])) or '-':<22} "
                  f"improve: {e.get('improve', '')}")
        return
    if args.action == "show":
        e = stash.get(args.name)
        print(yaml.safe_dump({args.name: e}, sort_keys=False) if e else f"{args.name} not in stash")
        return
    if args.action == "rm":
        print("removed" if stash.remove(args.name) else "not in stash")
        return
    # add: pull failed gates + metrics from the last verdict if available
    v = _load_verdicts().get(args.name, {})
    file = REGISTRY[args.name].__module__.split(".")[-1] + ".py" if args.name in REGISTRY else ""
    stash.add(args.name, file=file, params=v.get("params", {}),
              failed_gates=v.get("failed_gates", []), metrics=v.get("metrics", {}),
              improve=args.improve, stashed=date.today().isoformat())
    print(f"stashed {args.name} (gates: {','.join(v.get('failed_gates', [])) or 'manual'})")


def cmd_promote(args):
    v = _load_verdicts().get(args.name)
    if not args.force and (not v or not v.get("overall")):
        raise SystemExit(
            f"refusing to promote {args.name}: latest validate verdict is "
            f"{'missing' if not v else 'FAIL ' + str(v.get('failed_gates'))}. "
            f"Run `strategy.py validate {args.name}` first (or --force to override).")
    if args.name in _deployed():
        raise SystemExit(f"{args.name} is already a book in {PORTFOLIO}")
    block = (f"  - strategy: {args.name}\n"
             f"    symbol: {args.symbol}\n"
             f"    timeframe: {args.timeframe}\n"
             f"    weight: {args.weight}\n")
    text = PORTFOLIO.read_text()
    if "books:" not in text:
        raise SystemExit(f"{PORTFOLIO} has no `books:` list to append to")
    PORTFOLIO.write_text(text.rstrip("\n") + "\n" + block)
    stash.remove(args.name)  # promoted out of the stash if it was there
    print(f"promoted {args.name} → {PORTFOLIO} (weight={args.weight}). "
          f"Re-check book weights sum to ~1 and run validate_books.py.")


def cmd_new(args):
    dest = STRAT_DIR / f"{args.name}.py"
    if dest.exists():
        raise SystemExit(f"{dest} already exists")
    dest.write_text((STRAT_DIR / "strategy_template.py").read_text())
    print(f"created {dest}\nNext: rename the class, set name=\"{args.name}\", write "
          f"signal(), uncomment @register, add `from . import {args.name}` to "
          f"strategies/__init__.py, then: strategy.py validate {args.name}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--symbol", default="XAUUSD")
        sp.add_argument("--every", default="5m")

    sub.add_parser("list").set_defaults(func=cmd_list)

    d = sub.add_parser("diagnose"); d.add_argument("name", choices=sorted(REGISTRY))
    add_common(d); d.set_defaults(func=cmd_diagnose)

    v = sub.add_parser("validate"); v.add_argument("name", choices=sorted(REGISTRY))
    add_common(v); v.add_argument("--set", nargs="*", help="param overrides k=v")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("stash")
    s.add_argument("action", choices=["add", "list", "show", "rm"])
    s.add_argument("name", nargs="?")
    s.add_argument("--improve", default="", help="improvement idea for an `add`")
    s.set_defaults(func=cmd_stash)

    pr = sub.add_parser("promote"); pr.add_argument("name", choices=sorted(REGISTRY))
    pr.add_argument("--weight", type=float, required=True)
    pr.add_argument("--symbol", default="XAUUSD")
    pr.add_argument("--timeframe", default="M5")
    pr.add_argument("--force", action="store_true", help="bypass the validate gate")
    pr.set_defaults(func=cmd_promote)

    n = sub.add_parser("new"); n.add_argument("name"); n.set_defaults(func=cmd_new)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
