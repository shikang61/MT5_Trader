"""Refuse to start a second copy of a bot — a duplicate trading process means
double orders / double positions. Uses an OS advisory file lock keyed by name,
held for the process lifetime via the returned handle. The OS releases the lock
automatically when the process exits, even on a crash, so there is no stale-lock
problem (unlike a bare PID file).

    _lock = acquire_or_exit("portfolio")   # keep _lock alive for the run

Cross-platform: fcntl.flock on POSIX, msvcrt.locking on Windows (the VPS).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def acquire_or_exit(name: str, lock_dir: str | Path = "logs"):
    """Take the named lock or exit(1) if another holder is alive. Returns the
    open file handle — the CALLER MUST keep it referenced for the lock to hold."""
    Path(lock_dir).mkdir(parents=True, exist_ok=True)
    path = Path(lock_dir) / f".{name}.lock"
    handle = open(path, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        sys.exit(f"[single_instance] another '{name}' is already running "
                 f"({path} is locked) — refusing to double-launch.")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle
