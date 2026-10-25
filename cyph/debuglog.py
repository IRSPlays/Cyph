"""A debug log that survives restarts, for finding out why the arm agent, the arm or a task failed.

`cyph/data/arm_debug.log` (rotating: 2 MB x 4 files), one line per event with the time to the millisecond and the thread.
It records: startup (versions, port, arguments without any secret), the link to the server up and down with the reason,
every command the app sent and what happened to it, every task and every tool call the model made with its arguments,
result and how long it took, the arm's serial port dropping and coming back, the camera's state changes, a status line
every few seconds (every second during a task), and any exception that is not caught, in any thread. A hard crash of
Python itself (a native fault) goes to `arm_fault.log` next to it. Tokens and keys are never written. Voice is never
written: only that it started or stopped, never what was said.

Read it with:  python -m cyph.debuglog [lines]   (default 80)
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "data"
LOG = DIR / "arm_debug.log"
FAULT = DIR / "arm_fault.log"
log = logging.getLogger("cyph.debug")
_ready = False
_fault_file = None


def setup() -> logging.Logger:
    """Idempotent. Never raises: if the file cannot be opened the log is simply silent."""
    global _ready, _fault_file
    if _ready:
        return log
    _ready = True
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(_Fmt("%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.DEBUG)
        log.propagate = False
    except Exception:
        return log
    try:
        _fault_file = open(FAULT, "a", buffering=1)
        _fault_file.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} ---\n")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:
        pass

    prev = sys.excepthook

    def hook(t, v, tb):
        log.critical("uncaught exception in the main thread", exc_info=(t, v, tb))
        prev(t, v, tb)

    sys.excepthook = hook

    def thook(a):
        if a.exc_type is not SystemExit:
            log.critical("uncaught exception in thread %s", getattr(a.thread, "name", "?"), exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    threading.excepthook = thook
    log.info("=" * 70)
    log.info("start pid=%s python=%s %s %s", os.getpid(), platform.python_version(), platform.system(), platform.release())
    return log


class _Fmt(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        t = time.localtime(record.created)
        return f"{time.strftime('%Y-%m-%d %H:%M:%S', t)}.{int(record.msecs):03d}"


def dbg(msg, *a):
    log.debug(msg, *a)


def info(msg, *a):
    log.info(msg, *a)


def warn(msg, *a):
    log.warning(msg, *a)


def exc(msg, *a):
    """An exception that was caught but should still be found later: message plus the traceback."""
    log.error(msg, *a, exc_info=True)


def tail(n=80) -> str:
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(no debug log yet)"


if __name__ == "__main__":
    print(tail(int(sys.argv[1]) if len(sys.argv) > 1 else 80))
