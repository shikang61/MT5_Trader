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
