"""Singleton guard: a second holder of the same lock name must be refused."""
import threading
import time

import pytest

from mt5_trader.live.single_instance import acquire_or_exit


def test_second_acquire_is_refused(tmp_path):
    h = acquire_or_exit("bot", tmp_path)
    with pytest.raises(SystemExit):
        acquire_or_exit("bot", tmp_path, wait_s=0)   # same name, still held -> refuse
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


def test_waits_out_a_dying_holder(tmp_path):
    """Supervisor-restart race: the old process releases the lock a moment
    after the new one starts — the retry window must ride it out."""
    h = acquire_or_exit("bot", tmp_path)
    t = threading.Timer(0.8, h.close)       # old holder dies shortly
    t.start()
    start = time.monotonic()
    h2 = acquire_or_exit("bot", tmp_path, wait_s=5, poll_s=0.1)
    assert time.monotonic() - start < 5     # acquired well before the deadline
    t.join()
    h2.close()
