"""Which devices may talk to the server. A device pairs once with a one-time code (made on the server with
`python -m cyph.core pair`), receives a long random token, and can be revoked at any time. Only a hash of the token's
secret is stored; the secret itself is shown to the device once."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from pathlib import Path

KINDS = ("app", "agent", "telegram")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O/1/I
SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, token_hash TEXT NOT NULL,
  created REAL NOT NULL, last_seen REAL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pair_codes(code_hash TEXT PRIMARY KEY, expires REAL NOT NULL, kind TEXT NOT NULL);
"""


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _norm(code: str) -> str:
    return "".join(c for c in str(code or "").upper() if c.isalnum())


class DeviceStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.mu = threading.Lock()

    def close(self):
        with self.mu:
            self.db.close()

    def new_pair_code(self, kind: str = "app", ttl_s: float = 600) -> str:
        kind = kind if kind in KINDS else "app"
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        with self.mu, self.db:
            self.db.execute("DELETE FROM pair_codes WHERE expires < ?", (self.clock(),))
            self.db.execute("INSERT INTO pair_codes VALUES (?,?,?)", (_h(code), self.clock() + ttl_s, kind))
        return f"{code[:4]}-{code[4:]}"

    def pair(self, code: str, name: str = ""):
        """(device_id, token) for a valid unused code, else None. The code is spent either way it matches."""
        key = _h(_norm(code))
        with self.mu, self.db:
            row = self.db.execute("SELECT kind FROM pair_codes WHERE code_hash=? AND expires>=?",
                                  (key, self.clock())).fetchone()
            if not row:
                return None
            self.db.execute("DELETE FROM pair_codes WHERE code_hash=?", (key,))
            did, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO devices(id,name,kind,token_hash,created) VALUES (?,?,?,?,?)",
                            (did, " ".join(str(name or "").split())[:40] or "device", row[0], _h(secret), self.clock()))
        return did, f"{did}.{secret}"

    def verify(self, token: str):
        did, _, secret = str(token or "").partition(".")
        if not did or not secret:
            return None
        with self.mu, self.db:
            row = self.db.execute("SELECT id,name,kind,token_hash FROM devices WHERE id=? AND revoked=0",
                                  (did,)).fetchone()
            if not row or not hmac.compare_digest(row[3], _h(secret)):
                return None
            self.db.execute("UPDATE devices SET last_seen=? WHERE id=?", (self.clock(), did))
        return {"id": row[0], "name": row[1], "kind": row[2]}

    def list(self) -> list[dict]:
        with self.mu:
            rows = self.db.execute("SELECT id,name,kind,created,last_seen,revoked FROM devices ORDER BY created").fetchall()
        return [{"id": r[0], "name": r[1], "kind": r[2], "created": r[3], "last_seen": r[4], "revoked": bool(r[5])}
                for r in rows]

    def revoke(self, device_id: str) -> bool:
        with self.mu, self.db:
            cur = self.db.execute("UPDATE devices SET revoked=1 WHERE id=? AND revoked=0", (device_id,))
            return cur.rowcount > 0
