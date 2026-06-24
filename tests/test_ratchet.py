from mt5_trader.stops import ratchet_mult


def test_below_trigger_uses_disaster_mult():
    assert ratchet_mult(1.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 8.0


def test_at_or_above_trigger_uses_ratchet():
    assert ratchet_mult(2.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 3.0
    assert ratchet_mult(5.0, 8.0, trigger_T=2.0, ratchet_R=3.0) == 3.0


def test_disabled_when_ratchet_nonpositive():
    assert ratchet_mult(5.0, 8.0, trigger_T=2.0, ratchet_R=0.0) == 8.0


def test_disabled_when_trigger_nonpositive():
    assert ratchet_mult(5.0, 8.0, trigger_T=0.0, ratchet_R=3.0) == 8.0


def test_volume_spike_tightens_when_configured():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=1.5, volume_spike=True) == 1.5


def test_volume_spike_ignored_when_tight_unset():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=0.0, volume_spike=True) == 3.0


def test_armed_but_no_spike_uses_loose_ratchet():
    assert ratchet_mult(5.0, 8.0, 2.0, 3.0, ratchet_R_tight=1.5, volume_spike=False) == 3.0
