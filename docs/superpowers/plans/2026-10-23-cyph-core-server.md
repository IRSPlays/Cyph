# Cyph Core Server Implementation Plan (plan 1 of the companion spec)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated HTTPS + WebSocket API to the existing 24/7 server so a device can pair, read and edit the plan, edit memory, and chat with Cyph (streamed, with plan tools), while Telegram keeps working unchanged.

**Architecture:** A new package `cyph/core/` runs as a thread inside the existing `python -m cyph.server` process, sharing one `Planner` (an extension of the existing `Schedule`), one `MemoryStore` and one `Brain` with the Telegram bot, so nothing races over the same files. The app talks to the `Brain` through a new `channel="app"` path (strong model, plan tools, mood tags kept, streamed). Devices pair with a one-time code and then use revocable bearer tokens; the server is assumed to be internet-facing (Playit), so TLS uses a self-signed certificate that the apps pin by fingerprint.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, pydantic, `cryptography`, sqlite3 (stdlib), existing `google-genai` client, unittest/pytest (`python -m pytest`).

**Standing rules for whoever executes this (from Haziq):**
- Do NOT `git commit` or `git push` unless Haziq asks in the session. If he does: author `IRSPlays <haziqshahzai2021@hotmail.com>` and **no Claude co-author line**. Where a task says "Checkpoint", that is a point where a commit would be natural, not an instruction to commit.
- Ask Haziq before downloading anything (Task 1 installs three pip packages: ask first).
- Keys live only in env vars or the untracked `cyph/.env`. Never log message text, tokens or memory contents.
- Prefer the Read/Edit/Write tools over shell for file work. Run Python tests as `python -m pytest <file> -v` from the `cyph/` folder.

**Adaptations from the spec (decided while reading the real code, keep them):**
1. The planner stays on the existing JSON file (`schedule.json`) instead of new SQLite tables. The Telegram bot, the PC sync and `MemoryStore.snapshot()` already read that file; `Planner` is a subclass of `Schedule`, so all of them keep working. SQLite is used only for devices and pair codes.
2. The API runs inside the server process (thread), not as a second service, so one `Planner` instance and one lock are shared with Telegram.
3. The app's brain tools in this plan are the plan tools and `day_note` only. Memory is already written by the curator; `mute.set` and `visual.show` arrive with the widget plan; `arm.*` with the arm plan.
4. Because the apps must pin a self-signed certificate, all network I/O in the app has to run in the Tauri (Rust) layer, not in webview `fetch`. The server therefore sends no CORS headers.

---

## File structure

Create:
- `cyph/core/__init__.py`: config (`CoreConfig`, `load_config`).
- `cyph/core/limiter.py`: failure counter with lockout.
- `cyph/core/devices.py`: SQLite device registry, one-time pair codes, token verify/revoke.
- `cyph/core/tls.py`: self-signed certificate + fingerprint.
- `cyph/core/planner.py`: `Planner(Schedule)`: extras, conflicts, update, per-day notes, range.
- `cyph/core/toolbox.py`: the brain's plan tools (OpenAI-format declarations + runner).
- `cyph/core/prompts.py`: the app's system prompt.
- `cyph/core/app.py`: FastAPI app factory (REST + `/chat` WebSocket).
- `cyph/core/run.py`: starts uvicorn with TLS in a thread.
- `cyph/core/__main__.py`: CLI: `pair`, `devices`, `revoke`.
- `docs/core-api.md`: the API contract for client authors.
- `requirements-server.txt`
- tests: `tests/test_core_limiter.py`, `test_core_devices.py`, `test_core_tls.py`, `test_core_planner.py`, `test_core_toolbox.py`, `test_core_brain.py`, `test_core_app.py`, `test_core_start.py`

Modify:
- `cyph/schedule.py`: add `"block"` to `KINDS`.
- `cyph/telegram.py`: `tidy(keep_tags)`, `Brain(toolbox=)`, `_agent_loop`, `_collect`, `reply(channel=, on_text=, on_event=)`, wire the API into `run_service`.
- `cyph/server.py`: `--check` imports the core.
- `deploy/deploy_server.py`: upload `cyph/core/*.py`, install the new dependencies, `core` and `pair` commands.

---

### Task 1: Package skeleton, dependencies, deploy file list

**Files:**
- Create: `cyph/core/__init__.py`, `requirements-server.txt`
- Modify: `deploy/deploy_server.py:68-90`

- [ ] **Step 1: Ask Haziq, then install the packages**

Ask: "Install fastapi, uvicorn and cryptography with pip on this PC (for tests)?" On yes:

Run: `python -m pip install fastapi uvicorn cryptography`
Expected: ends with `Successfully installed ...` (httpx is already in `requirements.txt`; the FastAPI test client needs it).

- [ ] **Step 2: Create `requirements-server.txt`**

```
httpx
google-genai
pillow
fastapi
uvicorn
cryptography
```

- [ ] **Step 3: Create `cyph/core/__init__.py`** (config comes in Task 11; keep imports light so importing the package never needs FastAPI)

```python
"""Cyph Core: the HTTPS + WebSocket API the apps talk to. It runs inside the server process (cyph.server) as a
thread, so it shares the planner, memory and brain with the Telegram bot instead of racing it for the same files.
"""
```

- [ ] **Step 4: Update `deploy/deploy_server.py` so the subpackage is uploaded and the packages are installed**

In `code()`, replace

```python
    files = {f"cyph/{p.name}": p for p in sorted((ROOT / "cyph").glob("*.py"))}
    ssh(f"mkdir -p {REMOTE}/app/data/user && cd {REMOTE}/app && rm -f cyph/*.py", check=False)
```

with

```python
    files = {f"cyph/{p.name}": p for p in sorted((ROOT / "cyph").glob("*.py"))}
    files.update({f"cyph/core/{p.name}": p for p in sorted((ROOT / "cyph" / "core").glob("*.py"))})
    ssh(f"mkdir -p {REMOTE}/app/data/user {REMOTE}/app/cyph/core && cd {REMOTE}/app && rm -f cyph/*.py cyph/core/*.py",
        check=False)
```

In `venv()`, replace `httpx google-genai pillow` with `httpx google-genai pillow fastapi uvicorn cryptography`.

- [ ] **Step 5: Verify the imports work**

Run: `python -c "import fastapi, uvicorn, cryptography, httpx, cyph.core; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Checkpoint** (files: `cyph/core/__init__.py`, `requirements-server.txt`, `deploy/deploy_server.py`)

---

### Task 2: Failure limiter

**Files:**
- Create: `cyph/core/limiter.py`
- Test: `tests/test_core_limiter.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from cyph.core.limiter import Limiter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestLimiter(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.lim = Limiter(max_fails=3, window_s=60, lock_s=900, clock=self.clock)

    def test_locks_after_max_fails(self):
        self.lim.fail("k")
        self.lim.fail("k")
        self.assertEqual(self.lim.blocked("k"), 0)
        self.lim.fail("k")
        self.assertGreater(self.lim.blocked("k"), 800)

    def test_lock_expires(self):
        for _ in range(3):
            self.lim.fail("k")
        self.clock.t = 901
        self.assertEqual(self.lim.blocked("k"), 0)

    def test_old_failures_age_out(self):
        self.lim.fail("k")
        self.lim.fail("k")
        self.clock.t = 61
        self.lim.fail("k")
        self.assertEqual(self.lim.blocked("k"), 0)

    def test_success_clears_and_keys_are_separate(self):
        self.lim.fail("a")
        self.lim.fail("a")
        self.lim.ok("a")
        self.lim.fail("a")
        self.assertEqual(self.lim.blocked("a"), 0)
        for _ in range(3):
            self.lim.fail("b")
        self.assertEqual(self.lim.blocked("a"), 0)
        self.assertGreater(self.lim.blocked("b"), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_limiter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cyph.core.limiter'`

- [ ] **Step 3: Write the implementation**

```python
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

    def fail(self, key: str):
        now = self.clock()
        with self._mu:
            hits = [t for t in self._fails.get(key, []) if now - t < self.window_s] + [now]
            if len(hits) >= self.max_fails:
                self._locked[key] = now + self.lock_s
                self._fails.pop(key, None)
            else:
                self._fails[key] = hits

    def ok(self, key: str):
        with self._mu:
            self._fails.pop(key, None)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core_limiter.py -v`
Expected: 4 passed

- [ ] **Step 5: Checkpoint** (`cyph/core/limiter.py`, `tests/test_core_limiter.py`)

---

### Task 3: Device registry and pairing

**Files:**
- Create: `cyph/core/devices.py`
- Test: `tests/test_core_devices.py`

- [ ] **Step 1: Write the failing test**

```python
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cyph.core.devices import DeviceStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestDevices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "devices.db"
        self.clock = Clock()
        self.d = DeviceStore(self.path, clock=self.clock)

    def tearDown(self):
        self.d.close()
        self.tmp.cleanup()

    def test_pair_code_is_one_time(self):
        code = self.d.new_pair_code()
        got = self.d.pair(code, "Pixel")
        self.assertIsNotNone(got)
        self.assertIsNone(self.d.pair(code, "Again"))

    def test_code_accepts_lowercase_and_no_dash(self):
        code = self.d.new_pair_code()
        self.assertIsNotNone(self.d.pair(code.lower().replace("-", ""), "Pixel"))

    def test_wrong_and_expired_codes(self):
        self.assertIsNone(self.d.pair("AAAA-AAAA", "x"))
        code = self.d.new_pair_code(ttl_s=60)
        self.clock.t += 61
        self.assertIsNone(self.d.pair(code, "x"))

    def test_verify_good_tampered_and_garbage(self):
        did, token = self.d.pair(self.d.new_pair_code(), "  My   Phone ")
        dev = self.d.verify(token)
        self.assertEqual((dev["id"], dev["name"], dev["kind"]), (did, "My Phone", "app"))
        bad = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertIsNone(self.d.verify(bad))
        for junk in ("", "x", "abc.", ".abc", "zz." + token.split(".", 1)[1]):
            self.assertIsNone(self.d.verify(junk))

    def test_revoke(self):
        did, token = self.d.pair(self.d.new_pair_code(), "Laptop")
        self.assertTrue(self.d.revoke(did))
        self.assertIsNone(self.d.verify(token))
        self.assertFalse(self.d.revoke(did))
        self.assertFalse(self.d.revoke("nope"))

    def test_code_decides_the_kind(self):
        _, tok = self.d.pair(self.d.new_pair_code(kind="agent"), "PC arm")
        self.assertEqual(self.d.verify(tok)["kind"], "agent")
        _, tok = self.d.pair(self.d.new_pair_code(kind="whatever"), "Odd")
        self.assertEqual(self.d.verify(tok)["kind"], "app")

    def test_token_is_stored_hashed_and_list_hides_it(self):
        did, token = self.d.pair(self.d.new_pair_code(), "Phone")
        secret = token.split(".", 1)[1]
        raw = sqlite3.connect(str(self.path)).execute("select token_hash from devices").fetchall()
        self.assertTrue(all(secret not in row[0] for row in raw))
        rows = self.d.list()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("token_hash", rows[0])
        self.assertEqual(rows[0]["id"], did)

    def test_last_seen_updates(self):
        _, token = self.d.pair(self.d.new_pair_code(), "Phone")
        self.assertIsNone(self.d.list()[0]["last_seen"])
        self.clock.t += 5
        self.d.verify(token)
        self.assertEqual(self.d.list()[0]["last_seen"], 1005.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_devices.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cyph.core.devices'`

- [ ] **Step 3: Write the implementation**

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core_devices.py -v`
Expected: 8 passed

- [ ] **Step 5: Checkpoint** (`cyph/core/devices.py`, `tests/test_core_devices.py`)

---

### Task 4: Self-signed certificate and fingerprint

**Files:**
- Create: `cyph/core/tls.py`
- Test: `tests/test_core_tls.py`

- [ ] **Step 1: Write the failing test**

```python
import ssl
import tempfile
import unittest
from pathlib import Path

try:
    import cryptography  # noqa: F401
except ImportError:                                           # pragma: no cover
    raise unittest.SkipTest("cryptography is not installed")

from cyph.core.tls import ensure_cert, fingerprint


class TestTls(unittest.TestCase):
    def test_cert_is_created_once_and_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert, key, fp = ensure_cert(Path(tmp) / "core")
            self.assertTrue(cert.exists() and key.exists())
            self.assertEqual(len(fp), 64)
            int(fp, 16)                                       # hex
            again = ensure_cert(Path(tmp) / "core")
            self.assertEqual(again[2], fp)                    # not regenerated
            self.assertEqual(fingerprint(cert), fp)
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(str(cert), str(key))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_tls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cyph.core.tls'`

- [ ] **Step 3: Write the implementation**

```python
"""A self-signed certificate for the API. The apps do not trust a certificate authority: they pin this
certificate's SHA-256 fingerprint, which is printed when you make a pair code. It lives in data/core/ on the
server and is made on first use (valid ten years)."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fingerprint(cert_path) -> str:
    der = ssl.PEM_cert_to_DER_cert(Path(cert_path).read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def ensure_cert(folder, hosts=("127.0.0.1",)):
    """(cert_path, key_path, sha256_hex). Creates the pair if it is missing."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / "core.crt", folder / "core.key"
    if not (cert.exists() and key.exists()):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cyph-core")])
        now = datetime.now(timezone.utc)
        san = x509.SubjectAlternativeName([x509.DNSName("cyph-core")]
                                          + [x509.IPAddress(ipaddress.ip_address(h)) for h in hosts])
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
             .add_extension(san, critical=False).sign(k, hashes.SHA256()))
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
        try:
            os.chmod(key, 0o600)
        except OSError:
            pass
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key, fingerprint(cert)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core_tls.py -v`
Expected: 1 passed

- [ ] **Step 5: Checkpoint** (`cyph/core/tls.py`, `tests/test_core_tls.py`)

---

### Task 5: Planner

**Files:**
- Modify: `cyph/schedule.py:24` (`KINDS`)
- Create: `cyph/core/planner.py`
- Test: `tests/test_core_planner.py`

- [ ] **Step 1: Allow the new "block" kind**

In `cyph/schedule.py` change

```python
KINDS = ("test", "class", "deadline", "event", "task", "reminder")
```

to

```python
KINDS = ("test", "class", "deadline", "event", "task", "reminder", "block")
```

- [ ] **Step 2: Write the failing test**

```python
import tempfile
import unittest
from datetime import date
from pathlib import Path

from cyph.core.planner import Planner

TODAY = date(2026, 10, 23)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Planner(Path(self.tmp.name) / "schedule.json")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, title, d="2026-10-24", t="09:00", mins=60, **kw):
        return self.p.create({"title": title, "date": d, "time": t, "duration_min": mins, **kw}, TODAY)

    def test_create_keeps_the_extras(self):
        res = self.add("Maths revision", mins=120, kind="block", notes="past papers", project="school")
        ev = res["event"]
        self.assertEqual((ev["status"], ev["notes"], ev["project"], ev["kind"]), ("planned", "past papers", "school", "block"))
        self.assertEqual(res["conflicts"], [])
        self.assertEqual(self.p.get(ev["id"])["project"], "school")

    def test_conflict_is_reported_but_not_blocked(self):
        a = self.add("Maths", mins=120)["event"]
        b = self.add("Parade", t="10:30")
        self.assertEqual([c["id"] for c in b["conflicts"]], [a["id"]])
        self.assertEqual(len(self.p.all()), 2)

    def test_back_to_back_is_not_a_conflict(self):
        self.add("Maths", mins=60)
        self.assertEqual(self.add("Parade", t="10:00")["conflicts"], [])

    def test_update_moves_and_ignores_itself(self):
        a = self.add("Maths", mins=120)["event"]
        res = self.p.update(a["id"], {"time": "10:00"}, TODAY)
        self.assertEqual((res["event"]["time"], res["event"]["date"], res["conflicts"]), ("10:00", "2026-10-24", []))
        self.assertIsNone(self.p.update("nope", {"time": "10:00"}, TODAY))

    def test_status_and_validation(self):
        a = self.add("Maths")["event"]
        self.assertEqual(self.p.update(a["id"], {"status": "done"}, TODAY)["event"]["status"], "done")
        with self.assertRaises(ValueError):
            self.p.update(a["id"], {"status": "finished"}, TODAY)
        for bad in ({"title": "", "date": "2026-10-24"}, {"title": "x", "date": "nope"},
                    {"title": "x", "date": "2026-10-24", "time": "25:00"}):
            with self.assertRaises(ValueError):
                self.p.create(bad, TODAY)

    def test_recurring_events_cannot_be_marked_done(self):
        ev = self.p.create({"title": "NCDCC", "date": "2026-10-27", "time": "15:00", "repeat": "weekly",
                            "days": ["tue"]}, TODAY)["event"]
        with self.assertRaises(ValueError):
            self.p.update(ev["id"], {"status": "done"}, TODAY)
        self.assertEqual(self.p.update(ev["id"], {"time": "15:30"}, TODAY)["event"]["time"], "15:30")

    def test_range_lists_occurrences_with_ids(self):
        a = self.add("Maths")["event"]
        self.add("Later", d="2026-10-30")
        rows = self.p.range(date(2026, 10, 24), 3)
        self.assertEqual([r["title"] for r in rows], ["Maths"])
        self.assertEqual((rows[0]["id"], rows[0]["date"], rows[0]["time"]), (a["id"], "2026-10-24", "09:00"))
        self.assertEqual(self.p.day(date(2026, 10, 24))["events"][0]["title"], "Maths")

    def test_day_notes_persist(self):
        self.assertEqual(self.p.get_note(TODAY), "")
        self.p.set_note(TODAY, "  focus on maths ")
        again = Planner(Path(self.tmp.name) / "schedule.json")
        self.assertEqual(again.get_note(TODAY), "focus on maths")
        self.assertEqual(again.day(TODAY)["note"], "focus on maths")
        again.set_note(TODAY, "")
        self.assertEqual(again.get_note(TODAY), "")
        self.assertEqual(len(again.set_note(TODAY, "x" * 5000)), 2000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_core_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cyph.core.planner'`

- [ ] **Step 4: Write the implementation**

```python
"""The planner the apps use. It is the existing Schedule (same schedule.json, same ids, so Telegram, the PC sync and
the prompt snapshot keep working) plus what the app needs: notes, a status, conflict reporting, partial updates,
a range query and a free-form note per day (day_notes.json)."""
from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from ..clock import TZ
from ..schedule import Schedule, _parse_time, clean_event

STATUS = ("planned", "done", "skipped")
EDITABLE = {"title", "date", "time", "duration_min", "kind", "notes", "project", "status", "repeat", "days", "until"}
DEFAULT_MIN = 60                  # length assumed for a timed event with no duration, when looking for clashes
MAX_NOTE = 2000


def occ_dict(when: datetime, e: dict) -> dict:
    return {"id": e["id"], "title": e["title"], "date": when.date().isoformat(), "time": e.get("time"),
            "start": when.isoformat(), "duration_min": e.get("duration_min"), "kind": e.get("kind", "event"),
            "status": e.get("status", "planned"), "notes": e.get("notes", ""), "project": e.get("project", ""),
            "repeat": e.get("repeat", "none")}


class Planner(Schedule):
    def __init__(self, path=None, notes_path=None):
        super().__init__(path)
        self.notes_path = Path(notes_path) if notes_path else self.path.with_name("day_notes.json")

    # ---- events -----------------------------------------------------------------------------------
    def get(self, eid: str):
        return next((e for e in self._read() if e["id"] == eid), None)

    def make(self, raw: dict, today: date | None = None) -> dict:
        """A validated event with the app's extras; raises ValueError on anything unusable."""
        ev = clean_event(raw, today)
        status = raw.get("status") or "planned"
        if status not in STATUS:
            raise ValueError("status must be one of " + ", ".join(STATUS))
        ev["status"] = status
        notes = " ".join(str(raw.get("notes") or "").split())[:500]
        project = " ".join(str(raw.get("project") or "").split())[:60]
        if notes:
            ev["notes"] = notes
        if project:
            ev["project"] = project
        return ev

    def conflicts(self, ev: dict, ignore_id: str | None = None) -> list[dict]:
        """Timed events on the same day that overlap this one (back to back is fine)."""
        if not ev.get("time"):
            return []
        d = date.fromisoformat(ev["date"])
        a0 = datetime.combine(d, _parse_time(ev["time"]), tzinfo=TZ)
        a1 = a0 + timedelta(minutes=ev.get("duration_min") or DEFAULT_MIN)
        day0 = datetime.combine(d, dtime(0, 0), tzinfo=TZ)
        out = []
        for when, e in self.occurrences(day0, day0 + timedelta(days=1) - timedelta(seconds=1)):
            if e["id"] == ignore_id or not e.get("time") or e.get("status") == "skipped":
                continue
            if a0 < when + timedelta(minutes=e.get("duration_min") or DEFAULT_MIN) and when < a1:
                out.append({"id": e["id"], "title": e["title"], "time": e["time"]})
        return out

    def create(self, raw: dict, today: date | None = None) -> dict:
        with self.lock:
            ev = self.make(raw, today)
            clash = self.conflicts(ev)
            return {"event": self.add(ev), "conflicts": clash}

    def update(self, eid: str, patch: dict, today: date | None = None):
        """Change some fields of one event. None if there is no such event; ValueError if the result is unusable."""
        with self.lock:
            old = self.get(eid)
            if old is None:
                return None
            patch = {k: v for k, v in patch.items() if k in EDITABLE}
            if "status" in patch and old.get("repeat", "none") != "none":
                raise ValueError("recurring events cannot be marked done; edit or remove them instead")
            ev = self.make({**old, **patch}, today)
            self.replace(eid, ev)
            return {"event": {**ev, "id": eid}, "conflicts": self.conflicts(ev, ignore_id=eid)}

    def range(self, start: date, days: int) -> list[dict]:
        a = datetime.combine(start, dtime(0, 0), tzinfo=TZ)
        return [occ_dict(w, e) for w, e in self.occurrences(a, a + timedelta(days=days) - timedelta(seconds=1))]

    def day(self, d: date) -> dict:
        return {"date": d.isoformat(), "events": [r for r in self.range(d, 1) if r["date"] == d.isoformat()],
                "note": self.get_note(d)}

    # ---- a free-form note per day -----------------------------------------------------------------
    def _notes(self) -> dict:
        try:
            data = json.loads(self.notes_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get_note(self, d: date) -> str:
        return str(self._notes().get(d.isoformat(), ""))

    def set_note(self, d: date, text: str) -> str:
        text = str(text or "").strip()[:MAX_NOTE]
        with self.lock:
            notes = self._notes()
            if text:
                notes[d.isoformat()] = text
            else:
                notes.pop(d.isoformat(), None)
            self.notes_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.notes_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.notes_path)
        return text
```

- [ ] **Step 5: Run the test to verify it passes, and that the old schedule tests still pass**

Run: `python -m pytest tests/test_core_planner.py -v`
Expected: 8 passed

Run: `python -m pytest tests -k schedule -v` (the existing schedule and proactive tests; if none match the name, run `python tests/test_schedule.py`)
Expected: all pass (the `"block"` kind addition must not break them)

- [ ] **Step 6: Checkpoint** (`cyph/schedule.py`, `cyph/core/planner.py`, `tests/test_core_planner.py`)

---

### Task 6: The brain's plan tools

**Files:**
- Create: `cyph/core/toolbox.py`
- Test: `tests/test_core_toolbox.py`

- [ ] **Step 1: Write the failing test**

```python
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cyph.clock import TZ
from cyph.core.planner import Planner
from cyph.core.toolbox import ToolBox

NOW = datetime(2026, 10, 23, 8, 41, tzinfo=TZ)


class TestToolBox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.planner = Planner(Path(self.tmp.name) / "schedule.json")
        self.tb = ToolBox(self.planner, now=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tool_names(self):
        names = {t["function"]["name"] for t in self.tb.tools()}
        self.assertEqual(names, {"plan_list", "plan_add", "plan_edit", "plan_remove", "plan_status", "day_note"})

    def test_add_list_edit_status_remove(self):
        res = self.tb.run("plan_add", {"title": "Maths", "date": "tomorrow", "time": "09:00", "duration_min": 120})
        self.assertEqual(res["added"]["date"], "2026-10-24")
        self.assertEqual(res["conflicts"], [])
        eid = res["added"]["id"]
        listed = self.tb.run("plan_list", {"start": "2026-10-24", "days": 2})
        self.assertEqual([e["id"] for e in listed["events"]], [eid])
        self.assertEqual(self.tb.run("plan_edit", {"id": eid, "time": "10:00"})["updated"]["time"], "10:00")
        self.assertEqual(self.tb.run("plan_status", {"id": eid, "status": "done"})["updated"]["status"], "done")
        self.assertEqual(self.tb.run("plan_remove", {"id": eid}), {"removed": "Maths"})
        self.assertEqual(self.tb.run("plan_list", {})["events"], [])

    def test_conflicts_are_reported(self):
        self.tb.run("plan_add", {"title": "Maths", "date": "today", "time": "09:00", "duration_min": 120})
        res = self.tb.run("plan_add", {"title": "Parade", "date": "today", "time": "10:00"})
        self.assertEqual([c["title"] for c in res["conflicts"]], ["Maths"])

    def test_problems_come_back_as_errors_not_exceptions(self):
        self.assertIn("error", self.tb.run("plan_add", {"title": "x", "date": "today", "time": "25:00"}))
        self.assertIn("error", self.tb.run("plan_add", {"date": "today"}))
        self.assertIn("error", self.tb.run("plan_remove", {"id": "nope"}))
        self.assertIn("error", self.tb.run("plan_edit", {"id": "nope", "time": "10:00"}))
        self.assertIn("error", self.tb.run("nonsense", {}))
        ev = self.tb.run("plan_add", {"title": "NCDCC", "date": "2026-10-27", "time": "15:00", "repeat": "weekly",
                                      "days": ["tue"]})["added"]
        self.assertIn("error", self.tb.run("plan_status", {"id": ev["id"], "status": "done"}))

    def test_day_note_and_describe(self):
        self.assertEqual(self.tb.run("day_note", {"date": "today", "text": "focus"}), {"date": "2026-10-23", "saved": True})
        self.assertEqual(self.planner.get_note(NOW.date()), "focus")
        res = self.tb.run("plan_add", {"title": "Maths", "date": "today", "time": "09:00"})
        self.assertEqual(self.tb.describe("plan_add", {}, res), "plan_add: Maths (2026-10-23 09:00)")
        self.assertTrue(self.tb.describe("plan_remove", {"id": "x"}, {"error": "no event with that id"}).startswith("plan_remove failed"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_toolbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cyph.core.toolbox'`

- [ ] **Step 3: Write the implementation**

```python
"""The tools Cyph can call from the app: read and change the plan, and write a note for a day. Declarations use the
same OpenAI function format as agent.openai_tools(), so GeminiClient accepts them unchanged. Every result is a small
dict; a problem comes back as {"error": ...} for the model to read, never as an exception."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..clock import TZ

_S = {"type": "string"}
_N = {"type": "integer"}


def _fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
                                              "parameters": {"type": "object", "properties": props, "required": req}}}


_EVENT = {"title": _S, "date": {"type": "string", "description": "YYYY-MM-DD, or today / tomorrow"},
          "time": {"type": "string", "description": "24-hour HH:MM, Singapore time; omit for an all-day item"},
          "duration_min": _N, "kind": {"type": "string", "description": "block, task, class, test, deadline, event or reminder"},
          "notes": _S, "project": _S}

TOOLS = [
    _fn("plan_list", "List his plan from a start date for some days. Always call this first to get ids.",
        {"start": {"type": "string", "description": "YYYY-MM-DD, today or tomorrow (default today)"},
         "days": {"type": "integer", "description": "1 to 31, default 7"}}, []),
    _fn("plan_add", "Add something to his plan. The result lists any events it overlaps with.",
        {**_EVENT, "repeat": {"type": "string", "description": "none, daily or weekly"},
         "days": {"type": "array", "items": _S, "description": "weekdays for a weekly repeat, e.g. [\"tue\"]"}},
        ["title", "date"]),
    _fn("plan_edit", "Change fields of one plan item by id (move it, rename it, change its length or notes).",
        {"id": _S, **_EVENT}, ["id"]),
    _fn("plan_remove", "Remove one plan item by id.", {"id": _S}, ["id"]),
    _fn("plan_status", "Mark a one-off plan item done, skipped or planned again.",
        {"id": _S, "status": {"type": "string", "description": "planned, done or skipped"}}, ["id", "status"]),
    _fn("day_note", "Save or replace a free-form note for a day (what he wants out of it). Empty text clears it.",
        {"date": {"type": "string", "description": "YYYY-MM-DD, today or tomorrow"}, "text": _S}, ["date", "text"]),
]
EDITABLE = ("title", "date", "time", "duration_min", "kind", "notes", "project")
ADD_KEYS = EDITABLE + ("repeat", "days", "until")


def _brief(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("id", "title", "date", "time", "duration_min", "kind", "status")}


class ToolBox:
    def __init__(self, planner, now=None):
        self.planner = planner
        self.now = now or (lambda: datetime.now(TZ))

    def tools(self) -> list[dict]:
        return TOOLS

    def _date(self, s) -> date:
        s = str(s or "").strip().lower()
        today = self.now().date()
        if s in ("", "today"):
            return today
        if s == "tomorrow":
            return today + timedelta(days=1)
        return date.fromisoformat(s)

    def run(self, name: str, args: dict, approve=None) -> dict:
        fn = getattr(self, "_" + name, None) if name in {t["function"]["name"] for t in TOOLS} else None
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(args or {})
        except (ValueError, KeyError, TypeError) as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def describe(self, name: str, args: dict, res: dict) -> str:
        """One short line for the app's tape ("plan_add: Maths (2026-10-24 09:00)")."""
        if "error" in res:
            return f"{name} failed: {str(res['error'])[:80]}"
        t = res.get("added") or res.get("updated") or {}
        what = t.get("title") or res.get("removed") or args.get("id") or ""
        when = " ".join(x for x in (t.get("date"), t.get("time")) if x)
        return f"{name}: {what}" + (f" ({when})" if when else "")

    # ---- tools ------------------------------------------------------------------------------------
    def _plan_list(self, a):
        days = max(1, min(int(a.get("days") or 7), 31))
        return {"events": self.planner.range(self._date(a.get("start")), days)[:40]}

    def _plan_add(self, a):
        raw = {k: a[k] for k in ADD_KEYS if a.get(k) not in (None, "")}
        raw["date"] = self._date(a.get("date")).isoformat()
        res = self.planner.create(raw, self.now().date())
        return {"added": _brief(res["event"]), "conflicts": res["conflicts"]}

    def _plan_edit(self, a):
        patch = {k: a[k] for k in EDITABLE if k in a}
        if "date" in patch:
            patch["date"] = self._date(patch["date"]).isoformat()
        return self._updated(self.planner.update(str(a["id"]), patch, self.now().date()))

    def _plan_status(self, a):
        return self._updated(self.planner.update(str(a["id"]), {"status": str(a["status"])}, self.now().date()))

    @staticmethod
    def _updated(res):
        if res is None:
            return {"error": "no event with that id"}
        return {"updated": _brief(res["event"]), "conflicts": res["conflicts"]}

    def _plan_remove(self, a):
        gone = self.planner.remove(str(a["id"]))
        return {"removed": gone[0]["title"]} if gone else {"error": "no event with that id"}

    def _day_note(self, a):
        d = self._date(a.get("date"))
        return {"date": d.isoformat(), "saved": bool(self.planner.set_note(d, a.get("text", "")))}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core_toolbox.py -v`
Expected: 5 passed

- [ ] **Step 5: Checkpoint** (`cyph/core/toolbox.py`, `tests/test_core_toolbox.py`)

---

### Task 7: The app channel of the Brain

The same `Brain` that answers Telegram gains a `channel="app"` path: strong model, plan tools, the app's system prompt, mood tags kept, streamed text and tool events. Memory, shared chat and curation stay shared. Defaults reproduce today's behaviour exactly.

**Files:**
- Create: `cyph/core/prompts.py`
- Modify: `cyph/telegram.py` (`tidy`, `Brain.__init__`, `_agent_loop`, `_collect`, `reply`)
- Test: `tests/test_core_brain.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cyph.bridge import SharedChat
from cyph.clock import TZ
from cyph.core.planner import Planner
from cyph.core.toolbox import ToolBox
from cyph.memory import MemoryStore
from cyph.router import BrainMode
from cyph.telegram import Brain

NOW = datetime(2026, 10, 23, 8, 41, tzinfo=TZ)


class FakeGemini:
    """Plays a script, one step per call: {"text": [pieces], "tools": [calls]}. Records what it was offered."""

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def available(self):
        return True

    async def stream(self, messages, tools):
        self.seen.append((messages, tools))
        step = self.script.pop(0)
        for piece in step.get("text", []):
            yield "text", piece
        yield "tools", step.get("tools", [])


class TestAppChannel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.planner = Planner(root / "schedule.json")
        self.shared = SharedChat(root / "chat.json")
        self.store = MemoryStore(root / "mem")

    def tearDown(self):
        self.tmp.cleanup()

    def brain(self, fake, **kw):
        return Brain(store=self.store, shared=self.shared, llm=fake, big=fake, router=None,
                     mode=BrainMode(Path(self.tmp.name) / "mode.json"),
                     toolbox=ToolBox(self.planner, now=lambda: NOW), **kw)

    def test_app_reply_uses_plan_tools_streams_and_keeps_mood_tags(self):
        call = {"id": "c1", "name": "plan_add",
                "arguments": json.dumps({"title": "Maths", "date": "tomorrow", "time": "09:00", "duration_min": 120})}
        fake = FakeGemini([{"text": ["[happy] On it. "], "tools": [call]}, {"text": ["[proud] Done, it's in."]}])
        deltas, events = [], []
        out = self.brain(fake).reply("put maths at 9 tomorrow", channel="app", on_text=deltas.append, on_event=events.append)
        self.assertEqual([e["title"] for e in self.planner.all()], ["Maths"])
        self.assertEqual("".join(deltas), "[happy] On it. [proud] Done, it's in.")
        self.assertEqual(out, "[happy] On it. [proud] Done, it's in.")
        self.assertEqual([(e["name"], e["ok"]) for e in events], [("plan_add", True)])
        self.assertIn("plan_add", events[0]["summary"])
        names = [t["function"]["name"] for t in fake.seen[0][1]]
        self.assertIn("plan_add", names)
        self.assertNotIn("write_file", names)                          # the app never gets the file tools
        self.assertIn("square brackets", fake.seen[0][0][0]["content"])  # the app's system prompt
        last = self.shared._read()[-1]
        self.assertEqual((last["role"], last["src"]), ("assistant", "app"))
        self.assertNotIn("[", last["content"])                         # the stored copy has no mood tags

    def test_telegram_channel_is_unchanged(self):
        fake = FakeGemini([{"text": ["[happy] hello there"]}])
        out = self.brain(fake).reply("hi")
        self.assertEqual(out, "hello there")                           # tags stripped, no tools offered
        self.assertIsNone(fake.seen[0][1])

    def test_app_reply_when_gemini_is_unavailable(self):
        fake = FakeGemini([])
        fake.available = lambda: False
        out = self.brain(fake).reply("hi", channel="app")
        self.assertTrue(out.startswith("[sad]"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_brain.py -v`
Expected: FAIL (`TypeError: Brain.__init__() got an unexpected keyword argument 'toolbox'`)

- [ ] **Step 3: Create `cyph/core/prompts.py`**

```python
"""The system prompt for the companion app. Plain strings and light imports only."""
from __future__ import annotations

from ..clock import TIME_RULE
from ..persona import LOCAL_MEMORY, PERSONA
from ..theme import MOOD_COLOR

MOODS = [m for m in MOOD_COLOR if m != "rest"]

APP_RULES = f"""You are talking to him in the Cyph companion app, in chat bubbles that may also be read aloud. Reply in
Cyph's warm, playful voice: two to four short sentences unless he asks for more. Begin every reply with exactly one
mood tag in square brackets, chosen from: {", ".join(MOODS)}. It drives your face, so put no other square brackets
anywhere. You can change his plan with the plan tools: call plan_list before you move, edit or remove anything so you
have the right id, and never say a change is done unless the tool result says so. If a result lists conflicts, tell
him which items clash and ask what he wants; do not move other items on your own. Tool times are Singapore time,
24-hour. Plain sentences: no markdown, no lists."""


def app_system_prompt(memory: str = "") -> str:
    parts = [PERSONA, APP_RULES, TIME_RULE]
    if memory.strip():
        parts.append(LOCAL_MEMORY + "\n\n" + memory.strip())
    return "\n\n".join(parts)
```

- [ ] **Step 4: Edit `cyph/telegram.py`: `tidy`**

Replace

```python
def tidy(text: str) -> str:
    """Strip tags, thinking and markdown stars; keep the line breaks of a longer answer."""
    text = _TAGS.sub("", _THINK.sub("", text)).replace("*", "")
```

with

```python
def tidy(text: str, keep_tags: bool = False) -> str:
    """Strip tags, thinking and markdown stars; keep the line breaks of a longer answer. The app keeps its mood tags."""
    text = _THINK.sub("", text)
    text = (text if keep_tags else _TAGS.sub("", text)).replace("*", "")
```

- [ ] **Step 5: Edit `Brain.__init__`**

Change the signature end `light_label="Qwen (local)"):` to `light_label="Qwen (local)", toolbox=None):` and after the line `self.agent, self.approve = agent, None        # file tools for Gemini; approve(text) -> bool asks him` add:

```python
        self.toolbox = toolbox                        # core.toolbox.ToolBox: the plan tools, used only for channel="app"
```

- [ ] **Step 6: Replace `_agent_loop`, `_collect` and `reply` in `cyph/telegram.py`**

Replace the whole of `_agent_loop` with:

```python
    async def _agent_loop(self, messages, toolbox=None, on_text=None, on_event=None) -> str:
        """Gemini with tools: up to six rounds of ask, run the tools it called, ask again. toolbox (the app's plan
        tools) replaces the file tools; on_text gets each piece of text as it streams, on_event each finished call."""
        from .agent import openai_tools
        loop = asyncio.get_running_loop()
        out = ""
        tools = toolbox.tools() if toolbox else openai_tools()
        run = toolbox.run if toolbox else self.agent.run
        for _ in range(6):
            text, calls = "", []
            async for kind, val in self.big.stream(messages, tools):
                if kind == "text":
                    text += val
                    if on_text:
                        on_text(val)
                else:
                    calls = val
            out += text
            if not calls:
                return out
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for c in calls]})
            for c in calls:
                try:
                    args = json.loads(c["arguments"] or "{}")
                except ValueError:
                    args = {}
                res = await loop.run_in_executor(None, run, c["name"], args, self.approve)
                self.log("info", f"tool: {c['name']}" + (f" -> {res['error'][:60]}" if "error" in res else ""))
                if on_event:
                    on_event({"name": c["name"], "ok": "error" not in res,
                              "summary": toolbox.describe(c["name"], args, res) if toolbox else c["name"]})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res)[:60000]})
        return out + "\n(I stopped after several steps.)"
```

Replace the whole of `_collect` with:

```python
    async def _collect(self, messages, use_big: bool = False, needs_gemini: bool = False,
                       toolbox=None, on_text=None, on_event=None) -> str:
        out = ""
        if use_big:
            try:
                if self.agent or toolbox:
                    out = await self._agent_loop(messages, toolbox, on_text, on_event)
                else:
                    async for kind, val in self.big.stream(messages, None):
                        if kind == "text":
                            out += val
                            if on_text:
                                on_text(val)
                self.last_big_ok = True
                return out
            except Exception as e:
                self.log("warn", f"router: Gemini failed ({type(e).__name__})")
                if out or needs_gemini or toolbox:
                    return out or "Gemini failed just now, so I could not read that. Try again in a moment."
                self._ensure()
        self.last_big_ok = False
        async for kind, val in self.llm.stream(messages, None):
            if kind == "text":
                out += val
        return out
```

Replace the whole of `reply` with:

```python
    def reply(self, text: str, attachments=(), channel: str = "tg", on_text=None, on_event=None) -> str:
        """channel "app" (the companion app): always the strong model with the plan tools, the app's system prompt, mood
        tags kept in the answer, and live on_text / on_event callbacks. Memory, shared chat and curation are shared with
        Telegram, so it is one conversation."""
        from .attach import to_content
        from .router import BIG_LABEL, Decision, decide_with_mode, log_switch
        app = channel == "app"
        with self.lock:
            if not self.caught_up and self.curator:
                self.caught_up = True
                threading.Thread(target=self._catch_up, daemon=True).start()
            content, images, files = to_content(text, list(attachments))
            if app:
                dec = Decision(True, "app")
            else:
                dec = (decide_with_mode(self.router, self.mode.get(), text, images=images, files=files)
                       if self.router else Decision(False, ""))
            if dec.big and not (self.big and self.big.available()):
                if any(p.get("type") == "file" for p in (content if isinstance(content, list) else [])):
                    return "I need Gemini to read PDFs and it is not available right now (no key, or today's cap is used)."
                if app:
                    return "[sad] I cannot think right now: Gemini is not available (no key, or today's cap is used)."
                dec = Decision(False, "")
            if dec.big:
                if not app:
                    log_switch("gemini", dec.reason + " (telegram)")
            else:
                self._ensure()
            src = "app" if app else "tg"
            note = text + (" [sent " + ("a photo" if images == 1 else f"{images} photos") + "]" if images else "") \
                + (" [sent a file]" if files else "")
            prev = self.shared.last_time()
            self.shared.append("user", note, src)
            if not self.transcript:
                self.started = time.time()
            self.transcript.append(("user", note))
            memory = self.store.snapshot() if self.store else ""
            if app:
                from .core.prompts import app_system_prompt
                system = app_system_prompt(memory)
            else:
                system = remote_system_prompt(memory)
            recalled = self.store.recall(text) if self.store else ""
            if recalled:
                system += "\n\n" + recalled
            earlier = self.summary.get() if self.summary else ""
            if earlier:
                system += ("\n\nEarlier in this conversation (a summary of messages that are no longer shown; "
                           "treat it as what you already know):\n" + earlier)
            if dec.big and not app:
                system += "\n\n" + TELEGRAM_BIG + ("\n\n" + self.agent.prompt() if self.agent else "")
            history = self.shared.load(14)[:-1] + [{"role": "user", "content": content}]
            msgs = stamp([{"role": "system", "content": system}] + history, prev=prev)
            raw = asyncio.run(self._collect(msgs, dec.big, bool(files), self.toolbox if app else None, on_text, on_event))
            out = tidy(raw) or "..."
            self.shared.append("assistant", out, src)
            if self.summary:
                threading.Thread(target=self.summary.maybe_update, args=(self.shared,), daemon=True).start()
            self.transcript.append(("cyph", out))
            self.turn_at = time.monotonic()
            if REMEMBER.search(text) or self._unsaved() >= CURATE_EVERY:
                threading.Thread(target=self._curate, daemon=True).start()
            if app:
                return tidy(raw, keep_tags=True) or "[confused] ..."
            return out + (f"\n\n(answered by {BIG_LABEL})"
                          if dec.big and self.last_big_ok and self.light_label != BIG_LABEL else "")
```

- [ ] **Step 7: Run the new tests, then the existing Telegram and router tests**

Run: `python -m pytest tests/test_core_brain.py -v`
Expected: 3 passed

Run: `python tests/test_telegram.py`
Expected: ends with `ALL TELEGRAM TESTS PASSED`

Run: `python tests/test_router.py`
Expected: ends with `ALL ROUTER TESTS PASSED`

- [ ] **Step 8: Checkpoint** (`cyph/core/prompts.py`, `cyph/telegram.py`, `tests/test_core_brain.py`)

---

### Task 8: API app, part 1: auth, health, pairing, devices

**Files:**
- Create: `cyph/core/app.py`
- Test: `tests/test_core_app.py`

- [ ] **Step 1: Write the failing test** (this file grows in Tasks 9 and 10)

```python
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cyph.core.app import Deps, create_app
from cyph.core.devices import DeviceStore
from cyph.core.limiter import Limiter
from cyph.core.planner import Planner
from cyph.memory import MemoryStore


class FakeBrain:
    def reply(self, text, attachments=(), channel="tg", on_text=None, on_event=None):
        assert channel == "app"
        on_event({"name": "plan_add", "ok": True, "summary": "plan_add: Maths"})
        for piece in ("[happy] hel", "lo"):
            on_text(piece)
        return "[happy] hello"


class AppCase(unittest.TestCase):
    auth_limiter = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = DeviceStore(root / "devices.db")
        self.planner = Planner(root / "schedule.json")
        self.store = MemoryStore(root / "mem")
        self.deps = Deps(devices=self.devices, planner=self.planner, store=self.store, brain=FakeBrain(),
                         log=lambda *_: None, pair_limiter=Limiter(5, 600, 900),
                         auth_limiter=self.auth_limiter or Limiter(30, 60, 300))
        self.client = TestClient(create_app(self.deps))

    def tearDown(self):
        self.devices.close()
        self.tmp.cleanup()

    def pair(self, name="Pixel"):
        r = self.client.post("/pair", json={"code": self.devices.new_pair_code(), "name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def headers(self, token=None):
        return {"Authorization": "Bearer " + (token or self.pair()["token"])}


class TestAuth(AppCase):
    def test_health_needs_no_login_and_docs_are_off(self):
        self.assertEqual(self.client.get("/health").json()["ok"], True)
        self.assertEqual(self.client.get("/docs").status_code, 404)

    def test_everything_else_needs_a_token(self):
        for path in ("/me", "/devices"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/me", headers={"Authorization": "Bearer nope.nope"}).status_code, 401)

    def test_pairing_then_me(self):
        p = self.pair("My Phone")
        r = self.client.get("/me", headers=self.headers(p["token"]))
        self.assertEqual(r.json()["device"]["name"], "My Phone")
        self.assertEqual(self.client.post("/pair", json={"code": "AAAA-AAAA", "name": "x"}).status_code, 403)

    def test_pair_code_guessing_is_locked_out(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/pair", json={"code": "AAAA-AAAA", "name": "x"}).status_code, 403)
        good = self.devices.new_pair_code()
        r = self.client.post("/pair", json={"code": good, "name": "x"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", r.headers)

    def test_list_and_revoke_devices(self):
        a, b = self.pair("A"), self.pair("B")
        rows = self.client.get("/devices", headers=self.headers(a["token"])).json()["devices"]
        self.assertEqual({r["name"] for r in rows}, {"A", "B"})
        self.assertNotIn("token_hash", rows[0])
        self.assertEqual(self.client.delete(f"/devices/{b['device_id']}", headers=self.headers(a["token"])).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=self.headers(b["token"])).status_code, 401)
        self.assertEqual(self.client.delete("/devices/nope", headers=self.headers(a["token"])).status_code, 404)


class TestAuthLockout(AppCase):
    auth_limiter = Limiter(3, 60, 300)

    def test_repeated_bad_tokens_lock_even_a_good_one(self):
        good = self.headers()
        for _ in range(3):
            self.assertEqual(self.client.get("/me", headers={"Authorization": "Bearer bad.bad"}).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=good).status_code, 429)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cyph.core.app'`

- [ ] **Step 3: Write the implementation** (REST for plan, notes and memory and the WebSocket are added in Tasks 9 and 10)

```python
"""The API the companion apps talk to (FastAPI). Everything except /health and /pair needs a device token
(Authorization: Bearer <id>.<secret>). The server is reachable from the internet through the tunnel, so: no
interactive docs, failures are counted and locked out, and message text, tokens and memory are never logged."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from ..clock import TZ
from ..memory import FILES, MemoryStore
from .devices import DeviceStore
from .limiter import Limiter
from .planner import Planner


@dataclass
class Deps:
    devices: DeviceStore
    planner: Planner
    store: MemoryStore
    brain: Any
    log: Any
    pair_limiter: Limiter
    auth_limiter: Limiter


class PairIn(BaseModel):
    code: str
    name: str = ""


def create_app(d: Deps) -> FastAPI:
    app = FastAPI(title="Cyph Core", docs_url=None, redoc_url=None, openapi_url=None)

    def locked(limiter: Limiter, key: str):
        wait = limiter.blocked(key)
        if wait:
            raise HTTPException(429, "too many failed attempts", headers={"Retry-After": str(int(wait) + 1)})

    def auth(authorization: Optional[str] = Header(default=None)) -> dict:
        locked(d.auth_limiter, "auth")
        raw = authorization or ""
        dev = d.devices.verify(raw[7:]) if raw.lower().startswith("bearer ") else None
        if not dev:
            d.auth_limiter.fail("auth")
            raise HTTPException(401, "not paired")
        return dev

    @app.get("/health")
    def health():
        return {"ok": True, "time": datetime.now(TZ).isoformat(timespec="seconds")}

    @app.post("/pair")
    def pair(body: PairIn):
        locked(d.pair_limiter, "pair")
        got = d.devices.pair(body.code, body.name)
        if not got:
            d.pair_limiter.fail("pair")
            raise HTTPException(403, "bad or expired code")
        d.pair_limiter.ok("pair")
        did, token = got
        d.log("info", f"core: paired device {did}")
        return {"device_id": did, "token": token}

    @app.get("/me")
    def me(dev: dict = Depends(auth)):
        return {"device": dev, "now": datetime.now(TZ).isoformat(timespec="seconds")}

    @app.get("/devices")
    def devices(dev: dict = Depends(auth)):
        return {"devices": d.devices.list()}

    @app.delete("/devices/{device_id}")
    def revoke(device_id: str, dev: dict = Depends(auth)):
        if not d.devices.revoke(device_id):
            raise HTTPException(404, "no such device")
        d.log("info", f"core: revoked device {device_id}")
        return {"ok": True}

    return app
```

(`date`, `Request`, `Response` and `FILES` are used by the next tasks; leaving the imports in now avoids re-editing the header.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core_app.py -v`
Expected: 6 passed

- [ ] **Step 5: Checkpoint** (`cyph/core/app.py`, `tests/test_core_app.py`)

---

### Task 9: API app, part 2: plan, day notes, memory

**Files:**
- Modify: `cyph/core/app.py` (add the models and routes before `return app`)
- Test: `tests/test_core_app.py` (append a class)

- [ ] **Step 1: Append the failing tests** (above `if __name__ == "__main__":`)

```python
class TestPlanAndMemory(AppCase):
    def setUp(self):
        super().setUp()
        self.h = self.headers()

    def test_plan_crud_and_conflicts(self):
        r = self.client.post("/plan", headers=self.h, json={"title": "Maths", "date": "2026-10-24", "time": "09:00",
                                                           "duration_min": 120, "kind": "block", "notes": "papers"})
        self.assertEqual(r.status_code, 200, r.text)
        ev = r.json()["event"]
        self.assertEqual((ev["title"], ev["status"], ev["notes"]), ("Maths", "planned", "papers"))
        clash = self.client.post("/plan", headers=self.h, json={"title": "Parade", "date": "2026-10-24", "time": "10:00"})
        self.assertEqual([c["title"] for c in clash.json()["conflicts"]], ["Maths"])
        rows = self.client.get("/plan?start=2026-10-24&days=2", headers=self.h).json()["events"]
        self.assertEqual([x["title"] for x in rows], ["Maths", "Parade"])
        r = self.client.patch(f"/plan/{ev['id']}", headers=self.h, json={"time": "08:00"})
        self.assertEqual(r.json()["event"]["time"], "08:00")
        r = self.client.post(f"/plan/{ev['id']}/status", headers=self.h, json={"status": "done"})
        self.assertEqual(r.json()["event"]["status"], "done")
        self.assertEqual(self.client.delete(f"/plan/{ev['id']}", headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete(f"/plan/{ev['id']}", headers=self.h).status_code, 404)
        self.assertEqual(self.client.patch("/plan/nope", headers=self.h, json={"time": "08:00"}).status_code, 404)

    def test_plan_day_and_memory_need_a_token(self):
        for path in ("/plan", "/day/2026-10-24", "/memory", "/memory/user"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.post("/plan", json={"title": "x", "date": "2026-10-24"}).status_code, 401)

    def test_bad_input_is_422(self):
        for body in ({"title": "x", "date": "nope"}, {"title": "x", "date": "2026-10-24", "time": "25:00"},
                     {"title": "", "date": "2026-10-24"}):
            self.assertEqual(self.client.post("/plan", headers=self.h, json=body).status_code, 422, body)
        self.assertEqual(self.client.get("/plan?start=nope", headers=self.h).status_code, 422)

    def test_day_notes(self):
        self.assertEqual(self.client.get("/day/2026-10-24", headers=self.h).json()["note"], "")
        r = self.client.put("/day/2026-10-24", headers=self.h, json={"text": "focus on maths"})
        self.assertEqual(r.json()["note"], "focus on maths")
        self.assertEqual(self.client.get("/day/2026-10-24", headers=self.h).json()["note"], "focus on maths")
        self.assertEqual(self.client.get("/day/not-a-date", headers=self.h).status_code, 422)

    def test_memory_list_read_and_edit(self):
        rows = self.client.get("/memory", headers=self.h).json()["files"]
        self.assertEqual({r["key"] for r in rows}, {"user", "memory", "people"})
        self.assertTrue(all(r["cap"] > 0 and r["used"] == 0 for r in rows))
        r = self.client.post("/memory/user", headers=self.h, json={"action": "add", "text": "Likes kopi"})
        self.assertEqual((r.json()["applied"], r.json()["rejected"]), (1, []))
        again = self.client.post("/memory/user", headers=self.h, json={"action": "add", "text": "Likes kopi"}).json()
        self.assertEqual((again["applied"], again["rejected"][0]["why"]), (0, "already known"))
        self.assertEqual(self.client.get("/memory/user", headers=self.h).json()["entries"], ["Likes kopi"])
        self.assertIn('"source": "app"', (self.store.root / "changes.log").read_text(encoding="utf-8"))
        self.assertEqual(self.client.get("/memory/nope", headers=self.h).status_code, 404)
        self.assertEqual(self.client.post("/memory/nope", headers=self.h, json={"action": "add", "text": "x"}).status_code, 404)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_app.py -v -k PlanAndMemory`
Expected: FAIL (404s, routes missing)

- [ ] **Step 3: Add the models** (after `PairIn` in `cyph/core/app.py`)

```python
class EventIn(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
    duration_min: Optional[int] = None
    kind: str = "event"
    notes: str = ""
    project: str = ""
    repeat: str = "none"
    days: list[str] = []
    until: Optional[str] = None


class EventPatch(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    duration_min: Optional[int] = None
    kind: Optional[str] = None
    notes: Optional[str] = None
    project: Optional[str] = None
    repeat: Optional[str] = None
    days: Optional[list[str]] = None
    until: Optional[str] = None


class StatusIn(BaseModel):
    status: str


class NoteIn(BaseModel):
    text: str = ""


class MemoryOp(BaseModel):
    action: str
    text: str = ""
    match: str = ""
```

- [ ] **Step 4: Add the routes** (inside `create_app`, immediately before `return app`)

```python
    def day_of(s: str) -> date:
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise HTTPException(422, "dates look like 2026-10-24")

    def today() -> date:
        return datetime.now(TZ).date()

    # ---- plan -------------------------------------------------------------------------------------
    @app.get("/plan")
    def plan(start: Optional[str] = None, days: int = 7, dev: dict = Depends(auth)):
        first = day_of(start) if start else today()
        days = max(1, min(days, 60))
        return {"start": first.isoformat(), "days": days, "events": d.planner.range(first, days)}

    @app.post("/plan")
    def plan_add(body: EventIn, dev: dict = Depends(auth)):
        try:
            return d.planner.create(body.model_dump(exclude_none=True), today())
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.patch("/plan/{event_id}")
    def plan_edit(event_id: str, body: EventPatch, dev: dict = Depends(auth)):
        try:
            res = d.planner.update(event_id, body.model_dump(exclude_unset=True), today())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if res is None:
            raise HTTPException(404, "no such event")
        return res

    @app.post("/plan/{event_id}/status")
    def plan_status(event_id: str, body: StatusIn, dev: dict = Depends(auth)):
        try:
            res = d.planner.update(event_id, {"status": body.status}, today())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if res is None:
            raise HTTPException(404, "no such event")
        return res

    @app.delete("/plan/{event_id}")
    def plan_remove(event_id: str, dev: dict = Depends(auth)):
        if not d.planner.remove(event_id):
            raise HTTPException(404, "no such event")
        return {"ok": True}

    @app.get("/day/{day}")
    def day_get(day: str, dev: dict = Depends(auth)):
        return d.planner.day(day_of(day))

    @app.put("/day/{day}")
    def day_put(day: str, body: NoteIn, dev: dict = Depends(auth)):
        when = day_of(day)
        d.planner.set_note(when, body.text)
        return d.planner.day(when)

    # ---- memory (the same store, secret filter and change log the curator uses) --------------------
    def key_of(key: str) -> str:
        if key not in FILES:
            raise HTTPException(404, "no such memory file")
        return key

    @app.get("/memory")
    def memory_list(dev: dict = Depends(auth)):
        out = []
        for key, (fname, heading, _cap) in FILES.items():
            used, cap = d.store.usage(key)
            out.append({"key": key, "file": fname, "heading": heading, "used": used, "cap": cap})
        return {"files": out}

    @app.get("/memory/{key}")
    def memory_read(key: str, dev: dict = Depends(auth)):
        used, cap = d.store.usage(key_of(key))
        return {"key": key, "entries": d.store.entries(key), "used": used, "cap": cap}

    @app.post("/memory/{key}")
    def memory_edit(key: str, body: MemoryOp, dev: dict = Depends(auth)):
        res = d.store.apply([{"file": key_of(key), "action": body.action, "text": body.text, "match": body.match}], "app")
        return {"applied": len(res["applied"]), "rejected": [{"why": why} for _op, why in res["rejected"]]}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_core_app.py -v`
Expected: 11 passed

- [ ] **Step 6: Checkpoint** (`cyph/core/app.py`, `tests/test_core_app.py`)

---

### Task 10: WebSocket chat

Protocol: the client connects to `/chat`, sends `{"type":"auth","token":"..."}` within 8 seconds, receives `{"type":"ready","device":name}`, then sends `{"type":"say","text":"..."}`. For each `say` the server sends zero or more `{"type":"delta","text":...}` and `{"type":"event","name":...,"ok":...,"summary":...}`, then `{"type":"final","text":...}` (or `{"type":"error","message":...}`). `{"type":"ping"}` gets `{"type":"pong"}`. Messages are answered one at a time, in order.

**Files:**
- Modify: `cyph/core/app.py`
- Test: `tests/test_core_app.py` (append a class)

- [ ] **Step 1: Append the failing tests**

```python
from starlette.websockets import WebSocketDisconnect


class TestChat(AppCase):
    def open(self, ws, token):
        ws.send_json({"type": "auth", "token": token})
        return ws.receive_json()

    def test_bad_token_is_closed(self):
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "auth", "token": "bad.bad"})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()

    def test_first_message_must_be_auth(self):
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "say", "text": "hi"})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()

    def test_a_chat_turn_streams_events_then_final(self):
        token = self.pair("Pixel")["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.assertEqual(self.open(ws, token), {"type": "ready", "device": "Pixel"})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})
            ws.send_json({"type": "say", "text": "put maths at 9"})
            got = []
            while True:
                msg = ws.receive_json()
                got.append(msg)
                if msg["type"] in ("final", "error"):
                    break
            self.assertEqual([m["type"] for m in got], ["event", "delta", "delta", "final"])
            self.assertEqual(got[0]["summary"], "plan_add: Maths")
            self.assertEqual("".join(m["text"] for m in got if m["type"] == "delta"), "[happy] hello")
            self.assertEqual(got[-1]["text"], "[happy] hello")

    def test_a_failed_brain_sends_an_error_and_the_socket_survives(self):
        class Broken:
            def reply(self, *a, **k):
                raise RuntimeError("boom")
        self.deps.brain = Broken()
        token = self.pair()["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.open(ws, token)
            ws.send_json({"type": "say", "text": "hi"})
            self.assertEqual(ws.receive_json()["type"], "error")
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_empty_say_is_ignored(self):
        token = self.pair()["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.open(ws, token)
            ws.send_json({"type": "say", "text": "   "})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_app.py -v -k Chat`
Expected: FAIL (the `/chat` route does not exist, so the connection is refused)

- [ ] **Step 3: Add the WebSocket route** (inside `create_app`, before `return app`; and add `import asyncio` and `from fastapi import WebSocket, WebSocketDisconnect` to the imports at the top)

```python
    # ---- chat -------------------------------------------------------------------------------------
    async def answer(ws: WebSocket, text: str):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def put(item):
            loop.call_soon_threadsafe(q.put_nowait, item)

        def work():
            try:
                return d.brain.reply(text, channel="app", on_text=lambda t: put({"type": "delta", "text": t}),
                                     on_event=lambda e: put({"type": "event", **e}))
            finally:
                put(None)

        fut = loop.run_in_executor(None, work)
        while (item := await q.get()) is not None:
            await ws.send_json(item)
        try:
            await ws.send_json({"type": "final", "text": await fut})
        except Exception as e:                                  # the text is never logged, only the kind of failure
            d.log("error", f"core: chat reply failed ({type(e).__name__})")
            await ws.send_json({"type": "error", "message": "Cyph could not answer just now."})

    @app.websocket("/chat")
    async def chat(ws: WebSocket):
        await ws.accept()
        dev = None
        if not d.auth_limiter.blocked("auth"):
            try:
                first = await asyncio.wait_for(ws.receive_json(), timeout=8)
                if isinstance(first, dict) and first.get("type") == "auth":
                    dev = d.devices.verify(str(first.get("token") or ""))
            except Exception:
                dev = None
            if not dev:
                d.auth_limiter.fail("auth")
        if not dev:
            await ws.close(code=4401)
            return
        await ws.send_json({"type": "ready", "device": dev["name"]})
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                elif kind == "say":
                    text = str(msg.get("text") or "").strip()[:4000]
                    if text:
                        await answer(ws, text)
        except WebSocketDisconnect:
            pass
```

- [ ] **Step 4: Run the whole API test file**

Run: `python -m pytest tests/test_core_app.py -v`
Expected: 16 passed

- [ ] **Step 5: Checkpoint** (`cyph/core/app.py`, `tests/test_core_app.py`)

---

### Task 11: Wire it into the server (config, thread, CLI, startup)

**Files:**
- Modify: `cyph/core/__init__.py` (config)
- Create: `cyph/core/run.py`, `cyph/core/__main__.py`
- Modify: `cyph/telegram.py` (`run_service`), `cyph/server.py` (`--check`)
- Test: `tests/test_core_start.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

import httpx

from cyph.core import CoreConfig, load_config
from cyph.core.planner import Planner
from cyph.core.run import start
from cyph.memory import MemoryStore


class TestConfig(unittest.TestCase):
    def test_defaults_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_config(Path(tmp) / "none.json"), CoreConfig())
            self.assertFalse(CoreConfig().enabled)
            p = Path(tmp) / "core.json"
            p.write_text(json.dumps({"enabled": True, "port": 9000, "public_address": "x.playit.gg:1234"}))
            cfg = load_config(p)
            self.assertEqual((cfg.enabled, cfg.host, cfg.port, cfg.public_address), (True, "127.0.0.1", 9000, "x.playit.gg:1234"))
            p.write_text("{broken")
            self.assertEqual(load_config(p), CoreConfig())


class TestStart(unittest.TestCase):
    def test_serves_https_with_the_pinned_certificate(self):
        root = Path(tempfile.mkdtemp())
        try:
            with socket.socket() as s:                       # find a free port first
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            handle = start(CoreConfig(enabled=True, port=port), store=MemoryStore(root / "mem"), brain=None,
                           planner=Planner(root / "schedule.json"), log=lambda *_: None, folder=root / "core")
            try:
                r = httpx.get(f"https://127.0.0.1:{port}/health", verify=str(root / "core" / "core.crt"), timeout=10)
                self.assertEqual(r.json()["ok"], True)
                self.assertEqual((len(handle.fingerprint), handle.port), (64, port))
            finally:
                handle.stop()
        finally:
            shutil.rmtree(root, ignore_errors=True)            # sqlite may still hold devices.db on Windows: ignore


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core_start.py -v`
Expected: FAIL with `ImportError: cannot import name 'CoreConfig'`

- [ ] **Step 3: Replace `cyph/core/__init__.py`**

```python
"""Cyph Core: the HTTPS + WebSocket API the apps talk to. It runs inside the server process (cyph.server) as a
thread, so it shares the planner, memory and brain with the Telegram bot instead of racing it for the same files.

Switched on per machine by data/core.json:
    {"enabled": true, "host": "127.0.0.1", "port": 8443, "public_address": "xyz.playit.gg:12345"}
host stays 127.0.0.1 when a tunnel agent on the same machine forwards to it; use 0.0.0.0 to also allow the home LAN.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..bridge import DATA

CONFIG = DATA / "core.json"


@dataclass
class CoreConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8443
    public_address: str = ""


def load_config(path=None) -> CoreConfig:
    try:
        raw = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
        return CoreConfig(bool(raw.get("enabled")), str(raw.get("host") or "127.0.0.1"),
                          int(raw.get("port") or 8443), str(raw.get("public_address") or ""))
    except (OSError, ValueError, AttributeError):
        return CoreConfig()
```

- [ ] **Step 4: Create `cyph/core/run.py`**

```python
"""Start the API in a background thread, with TLS, inside the server process."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Handle:
    server: object
    thread: threading.Thread
    fingerprint: str
    port: int

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def start(cfg, *, store, brain, planner, log, folder, ready_timeout: float = 10.0) -> Handle:
    import uvicorn
    from .app import Deps, create_app
    from .devices import DeviceStore
    from .limiter import Limiter
    from .tls import ensure_cert
    folder = Path(folder)
    cert, key, fp = ensure_cert(folder)
    deps = Deps(devices=DeviceStore(folder / "devices.db"), planner=planner, store=store, brain=brain, log=log,
                pair_limiter=Limiter(5, 600, 900), auth_limiter=Limiter(30, 60, 300))
    config = uvicorn.Config(create_app(deps), host=cfg.host, port=cfg.port, ssl_certfile=str(cert),
                            ssl_keyfile=str(key), log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None            # only the main thread may install them
    thread = threading.Thread(target=server.run, name="cyph-core", daemon=True)
    thread.start()
    deadline = time.time() + ready_timeout
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError(f"the core API did not start on {cfg.host}:{cfg.port} (port already in use?)")
    port = server.servers[0].sockets[0].getsockname()[1]
    log("info", f"core API listening on {cfg.host}:{port} (certificate sha256 {fp[:16]}...)")
    return Handle(server, thread, fp, port)
```

- [ ] **Step 5: Create `cyph/core/__main__.py`**

```python
"""python -m cyph.core pair [--agent]   make a one-time pair code (valid 10 minutes) and print how to use it
   python -m cyph.core devices          list paired devices
   python -m cyph.core revoke ID        revoke one

Run these on the server (deploy/deploy_server.py pair does it over SSH). The code is a secret: it is printed here
only, never logged."""
from __future__ import annotations

import sys
from datetime import datetime

from . import DATA, load_config
from .devices import DeviceStore
from .tls import ensure_cert


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    folder = DATA / "core"
    if cmd == "pair":
        cert, _key, fp = ensure_cert(folder)
        store = DeviceStore(folder / "devices.db")
        code = store.new_pair_code("agent" if "--agent" in argv else "app")
        addr = load_config().public_address or "(set public_address in data/core.json)"
        print(f"Pair code:     {code}   (one use, valid 10 minutes)")
        print(f"Server:        {addr}")
        print(f"Cert SHA-256:  {fp}")
        print(f"Pair link:     cyph://pair?host={addr}&fp={fp}&code={code}")
        return 0
    if cmd == "devices":
        for row in DeviceStore(folder / "devices.db").list():
            seen = datetime.fromtimestamp(row["last_seen"]).isoformat(" ", "seconds") if row["last_seen"] else "never"
            print(f"{row['id']}  {row['kind']:<8} {row['name']:<20} last seen {seen}" + ("  REVOKED" if row["revoked"] else ""))
        return 0
    if cmd == "revoke" and len(argv) > 1:
        print("revoked" if DeviceStore(folder / "devices.db").revoke(argv[1]) else "no such active device")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 6: Wire the API into `run_service` in `cyph/telegram.py`**

(a) Replace

```python
    sched = Schedule(store.root / "schedule.json")
```

with

```python
    from .core.planner import Planner
    sched = Planner(store.root / "schedule.json")            # a Schedule with the app's extras; one instance for bot and API
```

(and delete the now-unused line `from .schedule import Schedule` just above it).

(b) Immediately after the line `svc.proactive = Proactive(sched, lambda text: svc.api.send(svc.owner, text), log=log)` add:

```python
    if server:                                           # the apps' API, in this process, sharing planner/memory/brain
        from .core import DATA as CORE_DATA, load_config as load_core
        cfg = load_core()
        if cfg.enabled:
            try:
                from .core.run import start as start_core
                from .core.toolbox import ToolBox
                brain.toolbox = ToolBox(sched)
                start_core(cfg, store=store, brain=brain, planner=sched, log=log, folder=CORE_DATA / "core")
            except Exception as e:                       # the bot must keep working whatever the API does
                log("error", f"core API did not start: {type(e).__name__}: {e}")
```

- [ ] **Step 7: Update `--check` in `cyph/server.py`**

Replace

```python
        from . import agent, digest, memory, proactive, router, schedule, summary, telegram   # noqa: F401
```

with

```python
        from . import agent, digest, memory, proactive, router, schedule, summary, telegram   # noqa: F401
        from .core import app, devices, limiter, planner, prompts, run, tls, toolbox          # noqa: F401
```

- [ ] **Step 8: Run the new tests and the full core + Telegram suites**

Run: `python -m pytest tests/test_core_start.py -v`
Expected: 2 passed

Run: `python -m pytest tests/test_core_limiter.py tests/test_core_devices.py tests/test_core_tls.py tests/test_core_planner.py tests/test_core_toolbox.py tests/test_core_brain.py tests/test_core_app.py tests/test_core_start.py -q`
Expected: all pass

Run: `python tests/test_telegram.py` then `python tests/test_sync.py` (or `python -m pytest tests/test_sync.py -q`) then `python -m cyph.server --check`
Expected: `ALL TELEGRAM TESTS PASSED`; the sync tests pass; `imports ok | gemini key: ... | telegram token: ...`

- [ ] **Step 9: Checkpoint** (`cyph/core/*`, `cyph/telegram.py`, `cyph/server.py`, `tests/test_core_start.py`)

---

### Task 12: Deploy, verify live, document

**Files:**
- Modify: `deploy/deploy_server.py` (`core`, `pair` commands, docstring)
- Create: `docs/core-api.md`

- [ ] **Step 1: Add the two deploy commands**

Add `import json` to the imports of `deploy/deploy_server.py`. Add these functions after `migrate()`:

```python
def core():
    """core <host:port>: switch the apps' API on (data/core.json on the server). The argument is the Playit tunnel's
    public address, only printed with pair codes."""
    addr = sys.argv[2] if len(sys.argv) > 2 else ""
    body = json.dumps({"enabled": True, "host": "127.0.0.1", "port": 8443, "public_address": addr}).encode()
    ssh(f"mkdir -p {REMOTE}/app/data && cat > {REMOTE}/app/data/core.json", body)
    print("apps' API enabled on the server (127.0.0.1:8443). Run `service` to apply.")


def pair():
    print(ssh(f"cd {REMOTE}/app && {REMOTE}/venv/bin/python -m cyph.core pair").strip())
```

In `main()`, change the tuple to `("code", "venv", "check", "service", "migrate", "core", "pair")`, and add to the module docstring:

```
    python deploy/deploy_server.py core <host:port>   switch the apps' API on (Playit address is printed with pair codes)
    python deploy/deploy_server.py pair               make a one-time pair code for a new device
```

- [ ] **Step 2: Deploy and start** (this restarts the live Telegram bot for a few seconds)

Run, in order, from `cyph/`:
`python deploy/deploy_server.py code`
`python deploy/deploy_server.py venv`   (installs fastapi, uvicorn, cryptography on the server; ask Haziq first, it downloads)
`python deploy/deploy_server.py check`
`python deploy/deploy_server.py core ""`
`python deploy/deploy_server.py service`
`python deploy/deploy_server.py logs`

Expected: `uploaded N modules ...`; check prints `imports ok | ...`; service prints `active`; the logs contain both `bot @haz_clawd_bot is up (server mode: Gemini only)` and `core API listening on 127.0.0.1:8443`.

- [ ] **Step 3: Verify from this PC through an SSH tunnel (nothing is exposed yet)**

In one terminal (leave it running):
`ssh -i ~/.ssh/cyph_server -N -L 18443:127.0.0.1:8443 hazserver@192.168.0.149`

In another:
`curl -sk https://127.0.0.1:18443/health`
Expected: `{"ok":true,"time":"..."}`

`python deploy/deploy_server.py pair` → note the code, then:
`curl -sk -X POST https://127.0.0.1:18443/pair -H "Content-Type: application/json" -d "{\"code\":\"<CODE>\",\"name\":\"test\"}"`
Expected: `{"device_id":"...","token":"..."}`

`curl -sk https://127.0.0.1:18443/plan -H "Authorization: Bearer <TOKEN>"`
Expected: `{"start":"...","days":7,"events":[...]}` with the existing schedule. And the same call without the header: HTTP 401.

Revoke the test device: `ssh ... "cd ~/cyph-server/app && ~/cyph-server/venv/bin/python -m cyph.core devices"` then `... revoke <ID>`.

- [ ] **Step 4: Verify Telegram still works**

Haziq sends the bot one normal message (and one schedule message such as "dentist friday 4pm"). Expected: normal replies; the schedule message is added (the shared `Planner` now backs it). Check `python deploy/deploy_server.py logs` shows no new errors.

- [ ] **Step 5: Verify the live chat over the tunnel**

With a fresh pair token and the SSH tunnel up, run this once from the scratchpad (not the project):

```python
import asyncio, json, ssl, websockets
async def main(token):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    async with websockets.connect("wss://127.0.0.1:18443/chat", ssl=ctx) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        print(await ws.recv())
        await ws.send(json.dumps({"type": "say", "text": "what's on my plan for the next 3 days?"}))
        while True:
            msg = json.loads(await ws.recv()); print(msg)
            if msg["type"] in ("final", "error"): break
asyncio.run(main(input("token: ")))
```

Expected: `ready`, then `delta` pieces (and `event` lines for `plan_list`), then `final` starting with a `[mood]` tag. This is the real Gemini 3.8 call; note the usage cap.

- [ ] **Step 6: Playit (Haziq's own account; I cannot do this)**

In the Playit dashboard create a TCP tunnel forwarding to `127.0.0.1:8443` on the server's agent, note its public address, then re-run `python deploy/deploy_server.py core <that address>` and `service`. From a phone on mobile data (not the home Wi-Fi), the plan-3 spike will verify the pinned-certificate round trip. Until then the API is reachable only through SSH.

- [ ] **Step 7: Write `docs/core-api.md`**

```markdown
# Cyph Core API (v1)

HTTPS, self-signed certificate. Clients must pin the certificate's SHA-256 fingerprint (printed with the pair code)
and must do all network I/O outside the webview (Tauri/Rust), because a webview cannot pin. No CORS, no docs page.

Auth: `Authorization: Bearer <device_id>.<secret>`. 401 = not paired or revoked. 429 = locked out for a while
(`Retry-After` seconds).

| Method + path | Body | Result |
|---|---|---|
| GET `/health` | none (no auth) | `{ok, time}` |
| POST `/pair` | `{code, name}` (no auth) | `{device_id, token}`; 403 bad code; 429 too many tries |
| GET `/me` | | `{device, now}` |
| GET `/devices` / DELETE `/devices/{id}` | | `{devices:[{id,name,kind,created,last_seen,revoked}]}` / `{ok}` |
| GET `/plan?start=YYYY-MM-DD&days=N` | | `{start, days, events:[{id,title,date,time,start,duration_min,kind,status,notes,project,repeat}]}` |
| POST `/plan` | `{title, date, time?, duration_min?, kind?, notes?, project?, repeat?, days?, until?}` | `{event, conflicts:[{id,title,time}]}`; 422 on bad input |
| PATCH `/plan/{id}` | any of the same fields | `{event, conflicts}`; 404; 422 |
| POST `/plan/{id}/status` | `{status: planned\|done\|skipped}` | `{event, conflicts}` (not for recurring events) |
| DELETE `/plan/{id}` | | `{ok}`; 404 |
| GET / PUT `/day/{date}` | `{text}` | `{date, events, note}` |
| GET `/memory` | | `{files:[{key,file,heading,used,cap}]}` |
| GET `/memory/{key}` | | `{key, entries, used, cap}` (`key` = user, memory, people) |
| POST `/memory/{key}` | `{action: add\|replace\|remove, text?, match?}` | `{applied, rejected:[{why}]}` (secret filter, duplicates and size caps apply; logged as source `app`) |

## WebSocket `/chat`

1. Connect, then within 8 seconds send `{"type":"auth","token":"..."}`. Server replies `{"type":"ready","device":name}`
   or closes with code 4401.
2. Send `{"type":"say","text":"..."}` (max 4000 chars). Messages are answered one at a time, in order.
3. For each `say` the server sends `{"type":"delta","text"}` pieces and `{"type":"event","name","ok","summary"}` for
   each tool call as it finishes, then `{"type":"final","text"}` (or `{"type":"error","message"}`).
   `final.text` starts with one mood tag, e.g. `[happy]`; the moods are the keys of `cyph/theme.py` `MOOD_COLOR`.
4. `{"type":"ping"}` is answered with `{"type":"pong"}`.

Pairing: run `python deploy/deploy_server.py pair` on the PC. The code is valid for 10 minutes and works once.
```

- [ ] **Step 8: Checkpoint** (`deploy/deploy_server.py`, `docs/core-api.md`). This is the natural point for Haziq to ask for a commit and push.

---

## Self-review

**Spec coverage** (spec section → task):
- §3 architecture, Telegram as a client of the same brain → Tasks 7, 11 (one `Brain`, shared planner).
- §4 auth, pairing, revoke, lockout → Tasks 2, 3, 8; TLS + pinning fingerprint → Tasks 4, 11, 12.
- §4 brain tools → Task 6 (plan tools, day note). `memory.*`, `visual.show`, `mute.set`, `arm.*` are deliberately deferred (adaptation 3 at the top).
- §4 context discipline → the app prompt reuses `store.snapshot()` (capped files plus upcoming plan); no new context sources were added. A per-request token budget log is not in this plan: it belongs with the study/architecture plan, which is where context sources are added.
- §4 memory: app edits go through the same store/secret filter/`changes.log` (Task 9). Curator and digest reading Cyph's own lines (commitments) is **not** in this plan; it is in the memory-editor plan.
- §5 planner (events, day notes, conflicts, move history) → Task 5. "Move history rows" are not built: YAGNI until the app shows history; noted here as a conscious cut. Reminders table and the scheduler/proactivity are the scheduler plan.
- §7 voice, §6 app UI, §8 arm: later plans.
- §10 milestone 1 (core) → this plan. Milestone 0 (Android spike) is the next plan and needs the pair flow and `/health` from Tasks 8 and 12.

**Placeholder scan:** no TBD/TODO or "similar to task N". Two issues found in review and fixed in the plan: Task 11's test used a temp directory after its `with` block had closed it, and Task 8 had a test for routes that only exist after Task 9.

**Known unknowns (cannot be checked without running it):** the code in this plan was written against the real files but has not been executed. Expect small fixes when the tests run first; the tests are written so that failures point at the cause. Specifically watch: FastAPI/pydantic versions on the server's Python (the models use `Optional[...]` so Python 3.9 works), uvicorn starting inside a thread, and Windows file locks on `devices.db` during temp-dir cleanup.

**Type consistency:** `Planner.create/update` return `{"event", "conflicts"}` and `ToolBox`, `app.py` and the tests all use those keys. `Brain.reply(text, attachments=(), channel, on_text, on_event)` matches `FakeBrain` and `answer()`. `ToolBox.describe/tools/run` are the three methods `_agent_loop` calls. `Handle.fingerprint/port/stop` match `test_core_start.py`. Limiter keys are `"pair"` and `"auth"` throughout.
