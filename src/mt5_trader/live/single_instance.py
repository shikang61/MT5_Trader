"""Refuse to start a second copy of a bot — a duplicate trading process means
double orders / double positions. Uses an OS advisory file lock keyed by name,
held for the process lifetime via the returned handle. The OS releases the lock
automatically when the process exits, even on a crash, so there is no stale-lock
problem (unlike a bare PID file).

    _lock = acquire_or_exit("portfolio")   # keep _lock alive for the run

A busy lock is retried for `wait_s` before giving up: a supervisor restart
(Stop+Start-ScheduledTask) races the old process's asynchronous death, and
without the grace window the fresh instance exits on the dying holder's lock
(observed live 2026-07-06 — portfolio loop silently down until manual restart).

Cross-platform: fcntl.flock on POSIX, msvcrt.locking on Windows (the VPS).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _try_lock(path: Path):
    """One non-blocking lock attempt. Returns the held handle or None."""
    handle = open(path, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def acquire_or_exit(name: str, lock_dir: str | Path = "logs",
                    wait_s: float = 15.0, poll_s: float = 0.5):
    """Take the named lock, retrying for `wait_s`, or exit(1) if another holder
    is still alive after that. Returns the open file handle — the CALLER MUST
    keep it referenced for the lock to hold."""
    Path(lock_dir).mkdir(parents=True, exist_ok=True)
    path = Path(lock_dir) / f".{name}.lock"
    deadline = time.monotonic() + wait_s
    while True:
        handle = _try_lock(path)
        if handle is not None:
            handle.write(str(os.getpid()))
            handle.flush()
            return handle
        if time.monotonic() >= deadline:
            sys.exit(f"[single_instance] another '{name}' is already running "
                     f"({path} is locked) — refusing to double-launch.")
        time.sleep(poll_s)
