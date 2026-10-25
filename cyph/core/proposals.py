"""Things Cyph would like to remember, waiting for the owner's yes. The model can only PROPOSE a memory from the app;
nothing reaches USER.md / MEMORY.md / people.md until a person accepts it on the Memory page."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from ..memory import FILES, MAX_ENTRY, MemoryStore, Rejected

MAX_PENDING = 20


class Proposals:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [p for p in data if isinstance(p, dict) and p.get("id") and p.get("file") in FILES] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _write(self, items: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def list(self) -> list[dict]:
        return self._read()

    def add(self, file: str, text: str, why: str = "") -> dict:
        """Raises Rejected for a file that does not exist, or text the memory would refuse anyway (too long, a secret...)."""
        if file not in FILES:
            raise Rejected(f"unknown file {file!r}")
        clean = MemoryStore.clean(text)
        if len(clean) > MAX_ENTRY:
            raise Rejected("too long")
        with self.lock:
            items = self._read()
            if any(p["file"] == file and p["text"].lower() == clean.lower() for p in items):
                raise Rejected("already waiting")
            if len(items) >= MAX_PENDING:
                raise Rejected("too many are waiting for an answer")
            item = {"id": secrets.token_hex(4), "file": file, "text": clean, "why": " ".join(str(why or "").split())[:200], "t": time.time()}
            self._write(items + [item])
        return item

    def pop(self, pid: str) -> dict | None:
        with self.lock:
            items = self._read()
            hit = next((p for p in items if p["id"] == pid), None)
            if hit:
                self._write([p for p in items if p["id"] != pid])
            return hit
