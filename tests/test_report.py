import re

from mt5_trader.live.report import build_report

DAY = "2026-06-25"          # the day passed in; the report should echo it back
EQ_OPEN, EQ_NOW = 99236.0, 100025.13
DEALS = {
    1001: {"net": -46.08, "n": 16, "wins": 8, "best": 30.0, "worst": -25.0},
    2003: {"net": 29.44, "n": 7, "wins": 4, "best": 12.0, "worst": -5.0},
}
MODELED = {
    "DonchianBreakout:XAUUSD@M5": {"gross_pnl": 2126.0, "trades": 42, "wins": 26,
                                   "best": 410.0, "worst": -180.0},
    "VwapTrend:XAUUSD@M5": {"gross_pnl": -170.0, "trades": 30, "wins": 12,
                            "best": 60.0, "worst": -90.0},
    "_account": {"first_equity": 99236.0, "last_equity": 100025.13},
}


def test_headline_has_day_equity_and_total():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS, MODELED)
    assert DAY in msg
    assert "99,236" in msg and "100,025" in msg     # equity open -> now
    assert "+789.13" in msg                         # equity change
    assert "-16.64" in msg                          # total realized net (sum of deal nets)


def test_message_is_html_not_codeblock():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS, MODELED)
    assert "<b>GOLD DESK" in msg                     # bold title (parse_mode=HTML)
    assert "<pre>" not in msg                        # no blue code block
    assert not re.search(r"\dtr", msg)               # no trade-count tokens (e.g. "16tr")
    assert "Today" in msg and DAY in msg             # scope labelled today + the given date


def test_realized_section_per_magic_with_winrate():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS, MODELED)
    assert "Portfolio (all books)" in msg           # label for magic 1001
    assert "Passive consolidation" in msg           # label for magic 2003
    assert "50% win" in msg                          # 8/16
    assert "57% win" in msg                          # 4/7 -> round(57.14)


def test_modeled_section_is_labeled_pre_cost_and_sorted():
    msg = build_report(DAY, EQ_OPEN, EQ_NOW, DEALS, MODELED)
    assert "pre-cost" in msg.lower()                 # disclaimer present
    assert "Donchian" in msg and "Vwap" in msg
    assert msg.index("Donchian") < msg.index("Vwap")  # sorted by gross desc
    assert "best +410.00" in msg and "worst -180.00" in msg
    assert "62%" not in msg and "40%" not in msg     # by-strategy win rate removed (26/42, 12/30)


def test_empty_inputs_are_explicit_not_blank():
    msg = build_report(DAY, 0.0, 0.0, {}, {})
    assert "no broker data" in msg.lower()
    assert "no steps today" in msg.lower()
