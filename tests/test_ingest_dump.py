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


def test_load_ticks_multidir_mixed_schema(tmp_path):
    # thin feed (ts,symbol,bid,ask) + thick feed (+bid_sz,ask_sz) for one symbol
    ts = [datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)]
    thin = pl.DataFrame({"ts": ts, "symbol": ["EURUSD"], "bid": [1.17], "ask": [1.18]})
    thick = pl.DataFrame({"ts": ts, "symbol": ["EURUSD"], "bid": [1.19], "ask": [1.20],
                          "bid_sz": [100.0], "ask_sz": [200.0]})
    for name, df in (("thinfeed", thin), ("thickfeed", thick)):
        d = tmp_path / name / "EURUSD"; d.mkdir(parents=True)
        df.write_parquet(d / "x.parquet")
    out = load_ticks([tmp_path / "thinfeed", tmp_path / "thickfeed"], "EURUSD", drop_weekend=False)
    assert out.height == 2                          # both feeds concatenated
    assert out.columns == ["ts", "symbol", "bid", "ask"]   # common CORE cols only
