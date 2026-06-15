"""Kill-switch wiring: a tripped kill must flatten every symbol and halt,
before any book/bars processing. Exercises the live PortfolioRunner.step path
with a fake gateway (no MT5, no VPS)."""
from datetime import datetime, timezone

from mt5_trader.live.runner import Book, PortfolioRunner, RunnerCfg
from mt5_trader.risk.manager import RiskManager, RoundCfg

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


def test_kill_flattens_every_symbol_and_halts(tmp_path):
    gw = FakeGW(900_000)               # down 10% from the seeded start
    default = RoundCfg("default", None, None,
                       max_leverage=2.0, loss_limit=0.05, target_vol=0.3)
    rm = RiskManager([], default)
    books = [Book(strategy=object(), symbol="XAUUSD"),
             Book(strategy=object(), symbol="XAGUSD")]
    cfg = RunnerCfg(symbol="PORTFOLIO", log_path=tmp_path / "p.jsonl")
    runner = PortfolioRunner(gw, books, rm, cfg)

    rm.mark_round_start(NOW, 1_000_000)  # peak/start anchored high
    runner.step()

    assert runner.halted is True
    assert sorted(gw.flattened) == ["XAGUSD", "XAUUSD"]
    assert "KILL_SWITCH" in (tmp_path / "p.jsonl").read_text()
