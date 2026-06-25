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
    "_account": {"first_equity": 99236.0, "last_equity": 100025.13},
}


def test_headline_has_day_equity_and_total():
    msg = build_report("2026-06-25", 99236.0, 100025.13, DEALS, MODELED)
    assert "2026-06-25" in msg
    assert "99,236" in msg and "100,025" in msg     # equity open -> now
    assert "+789.13" in msg                         # equity change
    assert "-16.64" in msg                          # total realized net (sum of deal nets)


def test_message_is_html_monospace():
    msg = build_report("2026-06-25", 99236.0, 100025.13, DEALS, MODELED)
    assert "<b>GOLD DESK" in msg                     # bold title (parse_mode=HTML)
    assert "<pre>" in msg and "</pre>" in msg        # monospace body so columns align


def test_realized_section_per_magic_with_winrate():
    msg = build_report("2026-06-25", 99236.0, 100025.13, DEALS, MODELED)
    assert "Portfolio (all books)" in msg           # label for magic 1001
    assert "Passive consolidation" in msg           # label for magic 2003
    assert "50%" in msg                              # 8/16
    assert "57%" in msg                              # 4/7 -> round(57.14)


def test_modeled_section_is_labeled_pre_cost_and_sorted():
    msg = build_report("2026-06-25", 99236.0, 100025.13, DEALS, MODELED)
    assert "pre-cost" in msg.lower()                 # disclaimer present
    assert "Donchian" in msg and "Vwap" in msg
    assert msg.index("Donchian") < msg.index("Vwap")  # sorted by gross desc
    assert "↑+410.00" in msg and "↓-180.00" in msg   # compact best/worst


def test_empty_inputs_are_explicit_not_blank():
    msg = build_report("2026-06-25", 0.0, 0.0, {}, {})
    assert "no broker data" in msg.lower()
    assert "no steps today" in msg.lower()
