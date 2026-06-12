"""Canonical tick/depth schema.

The vendor's exact dump schema is TBD (released after the opening announcement).
Ingestion maps vendor columns to this canonical layout via config/schema_map.yaml.

Canonical tick row:
    ts        datetime[us, UTC]   quote timestamp
    symbol    str                 e.g. "XAUUSD"
    bid, ask  float               best bid/ask price
    bid_sz, ask_sz float          best bid/ask size
    bid_px_1..5, bid_sz_1..5      depth levels (1 = best)
    ask_px_1..5, ask_sz_1..5
"""

DEPTH_LEVELS = 5

CORE_COLUMNS = ["ts", "symbol", "bid", "ask", "bid_sz", "ask_sz"]


def depth_columns(levels: int = DEPTH_LEVELS) -> list[str]:
    cols: list[str] = []
    for side in ("bid", "ask"):
        for i in range(1, levels + 1):
            cols += [f"{side}_px_{i}", f"{side}_sz_{i}"]
    return cols


ALL_COLUMNS = CORE_COLUMNS + depth_columns()
