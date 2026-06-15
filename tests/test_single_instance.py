"""Singleton guard: a second holder of the same lock name must be refused."""
import pytest

from mt5_trader.live.single_instance import acquire_or_exit


def test_second_acquire_is_refused(tmp_path):
    h = acquire_or_exit("bot", tmp_path)
    with pytest.raises(SystemExit):
        acquire_or_exit("bot", tmp_path)   # same name, still held -> refuse
    h.close()


def test_different_names_coexist(tmp_path):
    a = acquire_or_exit("portfolio", tmp_path)
    b = acquire_or_exit("passive_pdhl", tmp_path)   # different name -> ok
    a.close()
    b.close()


def test_reacquire_after_release(tmp_path):
    h = acquire_or_exit("bot", tmp_path)
    h.close()                               # release
    h2 = acquire_or_exit("bot", tmp_path)   # now free -> ok
    h2.close()
