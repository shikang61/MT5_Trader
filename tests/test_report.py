import re

from mt5_trader.live.report import build_report

DAY = "2026-06-25"          # the day passed in; the report should echo it back
EQ_OPEN, EQ_NOW = 99236.0, 100025.13
DEALS = {
    1001: {"net": -46.08, "n": 16, "wins": 8, "best": 30.0, "worst": -25.0},
    2003: {"net": 29.44, "n": 7, "wins": 4, "best": 12.0, "worst": -5.0},
}


def test_headline_has_day_equity_and_total():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS)
    assert DAY in msg
    assert "99,236" in msg and "100,025" in msg     # equity open -> now
    assert "+789.13" in msg                         # equity change
    assert "-16.64" in msg                          # total realized net (sum of deal nets)


def test_message_is_html_not_codeblock():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS)
    assert "<b>GOLD DESK" in msg                     # bold title (parse_mode=HTML)
    assert "<pre>" not in msg                        # no blue code block
    assert not re.search(r"\dtr", msg)               # no cramped "16tr" tokens
    assert "Today" in msg and DAY in msg             # scope labelled today + the given date


def test_realized_section_counts_winrate_and_total():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS)
    assert "Portfolio (all books)" in msg           # label for magic 1001
    assert "Passive consolidation" in msg           # label for magic 2003
    assert "16 trades" in msg and "7 trades" in msg  # per-book trade counts
    assert "23 trades" in msg                        # total trades (16 + 7)
    assert "50% win" in msg and "57% win" in msg     # 8/16 and 4/7


def test_empty_inputs_are_explicit_not_blank():
    msg = build_report(DAY, 0.0, 0.0, {})
    assert "no trades closed today" in msg.lower()
