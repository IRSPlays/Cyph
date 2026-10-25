"""Counts failures per key and locks the key out for a while. In memory only: a restart forgives everyone, which is
fine for a one-user server. Behind the Playit tunnel every client shares one address, so callers use fixed keys
("pair", "auth") rather than client addresses."""
from __future__ import annotations

import threading
import time


class Limiter:
    def __init__(self, max_fails: int, window_s: float, lock_s: float, clock=time.monotonic):
        self.max_fails, self.window_s, self.lock_s, self.clock = max_fails, window_s, lock_s, clock
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._mu = threading.Lock()

    def blocked(self, key: str) -> float:
        """Seconds left on the lock; 0 if the key may try."""
        with self._mu:
            left = self._locked.get(key, 0.0) - self.clock()
            if left <= 0:
                self._locked.pop(key, None)
                return 0.0
            return left

    def fail(self, key: str) -> bool:
        """Count one failure. True if this call started a lock (so the caller can log it once)."""
        now = self.clock()
        with self._mu:
            hits = [t for t in self._fails.get(key, []) if now - t < self.window_s] + [now]
            if len(hits) >= self.max_fails:
                self._locked[key] = now + self.lock_s
                self._fails.pop(key, None)
                return True
            self._fails[key] = hits
            return False

    def ok(self, key: str):
        with self._mu:
            self._fails.pop(key, None)
