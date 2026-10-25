"""Tells every connected device when something on the server changed.

The Watcher polls file signatures (mtime + size) once a second, so every writer is covered with no changes to
them: the Telegram bot, the planner tools, the memory curator, the PC sync, a REST edit. The Hub fans a message out
to the async queues of the /events sockets. Messages are small: {"type":"changed","what":"plan"}. A client
refetches that one resource; nothing is ever sent as a diff, so there is nothing to drift."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable

QUEUE_MAX = 200


class Hub:
    def __init__(self):
        self._subs: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._mu = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        """Call from the event loop that will read the queue."""
        q: asyncio.Queue = asyncio.Queue(QUEUE_MAX)
        with self._mu:
            self._subs[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._mu:
            self._subs.pop(q, None)

    def count(self) -> int:
        with self._mu:
            return len(self._subs)

    def publish(self, msg: dict):
        """Safe from any thread. A subscriber that fell behind loses messages and is told to refetch everything."""
        with self._mu:
            subs = list(self._subs.items())
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, msg)
            except RuntimeError:                       # that loop is closed: the socket is gone
                self.unsubscribe(q)

    @staticmethod
    def _put(q: asyncio.Queue, msg: dict):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                while True:
                    q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait({"type": "changed", "what": "all"})


def signature(paths: list[Path]) -> tuple:
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


class Watcher:
    """`topics` maps a name to the files whose change means that name changed."""

    def __init__(self, hub: Hub, topics: dict[str, list[Path]], every: float = 1.0, log: Callable = lambda *_: None):
        self.hub, self.topics, self.every, self.log = hub, {k: [Path(p) for p in v] for k, v in topics.items()}, every, log
        self._last = {k: signature(v) for k, v in self.topics.items()}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll(self) -> list[str]:
        """Check once; publish and return the topics that changed since the last check."""
        changed = []
        for name, paths in self.topics.items():
            sig = signature(paths)
            if sig != self._last[name]:
                self._last[name] = sig
                changed.append(name)
                self.hub.publish({"type": "changed", "what": name})
        return changed

    def start(self):
        def run():
            while not self._stop.wait(self.every):
                try:
                    self.poll()
                except Exception as e:                      # never let the watcher die
                    self.log("warn", f"core: watcher error ({type(e).__name__})")
        self._thread = threading.Thread(target=run, name="cyph-watch", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
