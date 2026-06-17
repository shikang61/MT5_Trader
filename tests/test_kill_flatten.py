"""Kill-switch wiring: a tripped kill must flatten every symbol and stand down,
before any book/bars processing. Exercises the live PortfolioRunner.step path
with a fake gateway (no MT5, no VPS)."""
from datetime import datetime, timezone

from mt5_trader.live.runner import (
    Book, LiveRunner, PairRunner, PairRunnerCfg, PortfolioRunner, RunnerCfg,
)
from mt5_trader.risk.manager import RiskManager, Posture

NOW = datetime.now(timezone.utc)


class FakeGW:
    def __init__(self, equity):
        self._eq = equity
        self.flattened = []

    def equity(self):
        return self._eq

    def flatten(self, symbol, magic=None):
        self.flattened.append(symbol)

    def bars(self, *a, **k):           # must never be reached once kill trips
        raise AssertionError("kill must short-circuit before fetching bars")


def test_kill_flattens_every_symbol_and_stands_down(tmp_path):
    gw = FakeGW(900_000)               # down 10% from the seeded day-start
    posture = Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager(posture)
    books = [Book(strategy=object(), symbol="XAUUSD"),
             Book(strategy=object(), symbol="XAGUSD")]
    cfg = RunnerCfg(symbol="PORTFOLIO", log_path=tmp_path / "p.jsonl")
    runner = PortfolioRunner(gw, books, rm, cfg)

    rm.roll_day(NOW, 1_000_000)        # day-start anchored high
    runner.step()

    assert runner._stood_down is True
    assert sorted(gw.flattened) == ["XAGUSD", "XAUUSD"]
    assert "KILL_SWITCH" in (tmp_path / "p.jsonl").read_text()


def test_live_runner_kill_checks_before_bars(tmp_path):
    gw = FakeGW(900_000)               # bars() raises if reached
    posture = Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager(posture)
    cfg = RunnerCfg(symbol="XAUUSD", magic=1001, log_path=tmp_path / "l.jsonl")
    runner = LiveRunner(gw, object(), rm, cfg)   # strategy unused — kill short-circuits

    rm.roll_day(NOW, 1_000_000)        # day-start anchored high; equity 900k = -10%
    runner.step()

    assert runner._stood_down is True
    assert gw.flattened == ["XAUUSD"]
    assert "KILL_SWITCH" in (tmp_path / "l.jsonl").read_text()


def test_pair_runner_kill_checks_before_bars(tmp_path):
    gw = FakeGW(900_000)
    posture = Posture(max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager(posture)
    cfg = PairRunnerCfg(symbol="XAUUSD", symbol_b="XAGUSD", log_path=tmp_path / "pr.jsonl")
    runner = PairRunner(gw, object(), rm, cfg)

    rm.roll_day(NOW, 1_000_000)
    runner.step()

    assert runner._stood_down is True
    assert sorted(gw.flattened) == ["XAGUSD", "XAUUSD"]
    assert "KILL_SWITCH" in (tmp_path / "pr.jsonl").read_text()
