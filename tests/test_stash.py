from mt5_trader.pipeline import stash


def test_stash_roundtrip(tmp_path):
    p = tmp_path / "stash.yaml"
    assert stash.list_(p) == {}
    assert stash.get("foo", p) is None

    stash.add("foo", file="foo.py", params={"z_n": 60},
              failed_gates=["cost_stress"], metrics={"ret": -0.01},
              improve="try a passive entry", stashed="2026-06-16", path=p)

    e = stash.get("foo", p)
    assert e["failed_gates"] == ["cost_stress"]
    assert e["improve"] == "try a passive entry"
    assert e["params"] == {"z_n": 60}
    assert "foo" in stash.list_(p)

    # add a second, then remove the first
    stash.add("bar", improve="x", path=p)
    assert set(stash.list_(p)) == {"foo", "bar"}
    assert stash.remove("foo", p) is True
    assert stash.get("foo", p) is None
    assert set(stash.list_(p)) == {"bar"}
    assert stash.remove("foo", p) is False  # already gone
