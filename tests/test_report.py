import re

from mt5_trader.live.report import build_report

DAY = "2026-06-25"          # the session day passed in; the report should echo it back
EQUITY = 100025.08
DEALS = {
    1001: {"net": -46.08, "n": 16, "wins": 8, "best": 30.0, "worst": -25.0},
    2003: {"net": 29.44, "n": 7, "wins": 4, "best": 12.0, "worst": -5.0},
}


def test_headline_has_day_equity_and_total():
    msg = build_report(DAY, EQUITY, DEALS)
    assert DAY in msg
    assert "100,025.08" in msg                       # current equity (snapshot, no open/change)
    assert "-16.64" in msg                           # total realized net (sum of deal nets)


def test_message_is_html_not_codeblock():
    msg = build_report(DAY, EQUITY, DEALS)
    assert "<b>GOLD DESK" in msg                     # bold title (parse_mode=HTML)
    assert "<pre>" not in msg                        # no blue code block
    assert not re.search(r"\dtr", msg)               # no cramped "16tr" tokens
    assert "session" in msg.lower() and DAY in msg   # scope labelled session + the given date


def test_realized_section_counts_winrate_and_total():
    msg = build_report(DAY, EQUITY, DEALS)
    assert "Portfolio (all books)" in msg           # label for magic 1001
    assert "Passive consolidation" in msg           # label for magic 2003
    assert "16 trades" in msg and "7 trades" in msg  # per-book trade counts
    assert "23 trades" in msg                        # total trades (16 + 7)
    assert "50% win" in msg and "57% win" in msg     # 8/16 and 4/7


def test_strategies_section_lists_weights():
    weights = [("donchian", 0.40), ("sweep_fade", 0.15), ("asian_sweep", 0.10)]
    msg = build_report(DAY, EQUITY, DEALS, weights)
    assert "Live strategies" in msg
    assert "donchian: 40%" in msg
    assert "sweep_fade: 15%" in msg
    assert "asian_sweep: 10%" in msg


def test_empty_inputs_are_explicit_not_blank():
    msg = build_report(DAY, 0.0, {})
    assert "no trades closed this session" in msg.lower()
    assert "Live strategies" not in msg               # no weights passed -> no section
