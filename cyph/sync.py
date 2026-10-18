"""The PC's half of the sync with the 24/7 server (the server owns memory, the schedule and the shared chat).

- push(): sends what happened here since last time: memory edits (read back from changes.log as add / replace /
  remove operations), new chat messages, finished transcripts, a changed model mode. The server applies them
  with its own checks.
- pull(): replaces the local copy of memory, daily notes, schedule, chat, summary and mode with the server's.
- cycle() = push then pull, so nothing done here is lost by the pull.
- Tunnel: a reverse SSH tunnel that lets the server reach this PC's live Cyph (arm and camera included)
  while the app is running. It only ever listens on the server's own localhost.

Transport is SSH with a key (never a password). Without a server config, or when the server cannot be reached,
everything still works locally and the next cycle catches up.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from .memory import FILES

DATA = Path(__file__).resolve().parents[1] / "data"
CONFIG = DATA / "server.json"
STATE = DATA / "sync_state.json"
FNAME_TO_KEY = {v[0]: k for k, v in FILES.items()}
PULL_REPLACE = {"USER.md", "MEMORY.md", "people.md", "schedule.json", "recent_chat.json", "chat_summary.json"}


def load_config(path=None) -> dict | None:
    try:
        c = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
        return c if c.get("host") and c.get("user") and c.get("key") else None
    except (OSError, ValueError):
        return None


class SyncError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------------------
# transports: SSH to the real server, or a plain directory (used by the tests)
# ---------------------------------------------------------------------------------------------------------
class SshRunner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.remote = cfg.get("remote", "~/cyph-server")
        self.python = cfg.get("remote_python", "python3")

    def _argv(self, extra=()):
        c = self.cfg
        return ["ssh", "-i", os.path.expanduser(c["key"]), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                "-o", "ServerAliveInterval=10", *extra, f"{c['user']}@{c['host']}"]

    def _run(self, cmd: str, data: bytes | None = None, timeout: float = 90.0) -> bytes:
        try:
            r = subprocess.run(self._argv() + [f"cd {self.remote}/app && {self.python} -m cyph.sync_server {cmd}"],
                               input=data, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SyncError(f"cannot reach the server ({type(e).__name__})")
        if r.returncode != 0:
            raise SyncError("server command failed: " + r.stderr.decode("utf-8", "replace")[-300:].strip())
        return r.stdout

    def pack(self) -> bytes:
        return self._run("pack")

    def apply(self, payload: dict) -> dict:
        return json.loads(self._run("apply", json.dumps(payload).encode("utf-8")) or b"{}")

    def bridge(self, info: dict):
        self._run("bridge", json.dumps(info).encode("utf-8"))

    def unbridge(self):
        self._run("unbridge")

    def tunnel_argv(self, remote_port: int, local_port: int) -> list[str]:
        return self._argv(["-N", "-T", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveCountMax=3",
                           "-R", f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}"])


class LocalRunner:
    """The same interface against a directory on this machine (tests)."""

    def __init__(self, server_data: Path):
        self.data = Path(server_data)

    def pack(self) -> bytes:
        from . import sync_server
        return sync_server.pack(self.data)

    def apply(self, payload: dict) -> dict:
        from . import sync_server
        return sync_server.apply(self.data, payload)

    def bridge(self, info: dict):
        from . import sync_server
        sync_server.bridge(self.data, info)

    def unbridge(self):
        from . import sync_server
        sync_server.unbridge(self.data)


# ---------------------------------------------------------------------------------------------------------
class Sync:
    def __init__(self, runner, data_dir=None, log=lambda *_: None):
        self.runner, self.log = runner, log
        self.data = Path(data_dir) if data_dir else DATA
        self.user = self.data / "user"
        self.state_path = self.data / "sync_state.json"
        self.lock = threading.Lock()

    # ---- state ------------------------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"change_line": 0, "chat_t": 0.0, "uploaded": [], "mode_t": 0.0}

    def _save(self, st: dict):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st), encoding="utf-8")

    # ---- what to send -----------------------------------------------------------------------------
    def local_ops(self, since_line: int) -> tuple[list[dict], int]:
        """Memory edits made here, turned back into operations the server can replay."""
        log = self.user / "changes.log"
        if not log.exists():
            return [], since_line
        lines = log.read_text(encoding="utf-8").splitlines()
        ops = []
        for line in lines[since_line:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            key = FNAME_TO_KEY.get(r.get("file"))
            if key is None or str(r.get("source", "")).startswith("pc:"):
                continue
            act = r.get("action")
            if act == "add" and r.get("new"):
                ops.append({"file": key, "action": "add", "text": r["new"]})
            elif act == "replace" and r.get("old") and r.get("new"):
                ops.append({"file": key, "action": "replace", "match": r["old"], "text": r["new"]})
            elif act == "remove" and r.get("old"):
                ops.append({"file": key, "action": "remove", "match": r["old"]})
            # "rewrite" (a consolidation) is not replayed: the server consolidates its own files
        return ops, len(lines)

    def push(self) -> dict:
        with self.lock:
            st = self._state()
            ops, line = self.local_ops(int(st.get("change_line", 0)))
            chat = []
            try:
                chat = [m for m in json.loads((self.user / "recent_chat.json").read_text(encoding="utf-8"))
                        if float(m.get("t", 0)) > float(st.get("chat_t", 0))]
            except (OSError, ValueError):
                pass
            uploaded = set(st.get("uploaded", []))
            sessions = []
            for p in sorted((self.user / "sessions").glob("*.jsonl")) if (self.user / "sessions").is_dir() else []:
                if p.name not in uploaded and p.with_suffix(".done").exists():      # only finished (curated) ones
                    sessions.append({"name": p.name, "text": p.read_text(encoding="utf-8"), "done": True})
            payload = {"ops": ops, "chat": chat, "sessions": sessions, "source": "desktop"}
            mode_file = self.data / "brain_mode.json"
            if mode_file.exists() and mode_file.stat().st_mtime > float(st.get("mode_t", 0)):
                try:
                    payload["mode"] = {"mode": json.loads(mode_file.read_text(encoding="utf-8")).get("mode"),
                                       "t": mode_file.stat().st_mtime}
                except (OSError, ValueError):
                    pass
            if not (ops or chat or sessions or payload.get("mode")):
                return {"nothing": True}
            res = self.runner.apply(payload)
            st["change_line"] = line
            if chat:
                st["chat_t"] = max(float(m.get("t", 0)) for m in chat)
            st["uploaded"] = sorted(uploaded | {s["name"] for s in sessions})
            if payload.get("mode"):
                st["mode_t"] = payload["mode"]["t"]
            self._save(st)
            self.log("info", f"sync: sent {len(ops)} memory edit(s), {len(chat)} message(s), {len(sessions)} transcript(s)")
            return res

    # ---- what to take ------------------------------------------------------------------------------
    def pull(self) -> bool:
        with self.lock:
            blob = self.runner.pack()
            tmp = Path(tempfile.mkdtemp(prefix="cyph-pull-"))
            try:
                with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                    for m in tar.getmembers():
                        n = m.name
                        if not m.isfile() or n.startswith(("/", "\\")) or ".." in Path(n).parts:
                            raise SyncError(f"refusing a suspicious path in the server's data: {n!r}")
                    tar.extractall(tmp, filter="data")
                changed = False
                (self.user / "daily").mkdir(parents=True, exist_ok=True)
                (self.user / "sessions").mkdir(parents=True, exist_ok=True)
                for src in (tmp / "user").glob("*") if (tmp / "user").is_dir() else []:
                    if src.is_file() and src.name in PULL_REPLACE:
                        changed |= _copy_if_different(src, self.user / src.name)
                for src in (tmp / "user" / "daily").glob("*") if (tmp / "user" / "daily").is_dir() else []:
                    changed |= _copy_if_different(src, self.user / "daily" / src.name)
                for src in (tmp / "user" / "sessions").glob("*") if (tmp / "user" / "sessions").is_dir() else []:
                    if not (self.user / "sessions" / src.name).exists():            # never overwrite local transcripts
                        shutil.copy2(src, self.user / "sessions" / src.name)
                if (tmp / "brain_mode.json").is_file():
                    mf = self.data / "brain_mode.json"          # push ran first, so the server's is the newest
                    changed |= _copy_if_different(tmp / "brain_mode.json", mf)
                    st = self._state()
                    st["mode_t"] = mf.stat().st_mtime
                    self._save(st)
                return changed
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def cycle(self) -> bool:
        """push then pull. Returns True if anything local changed (the caller refreshes its prompt)."""
        try:
            self.push()
            changed = self.pull()
            if changed:
                self.log("info", "sync: took the server's newer memory, schedule and chat")
            return changed
        except SyncError as e:
            self.log("warn", f"sync: {e}; working locally, will catch up")
            return False


def _copy_if_different(src: Path, dst: Path) -> bool:
    if dst.exists() and dst.read_bytes() == src.read_bytes():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


# ---------------------------------------------------------------------------------------------------------
class Tunnel:
    """Keeps a reverse SSH tunnel up while the desktop app runs, and tells the server where it leads."""

    def __init__(self, runner: SshRunner, remote_port: int, bridge_info_fn, log=lambda *_: None):
        self.runner, self.remote_port, self.info_fn, self.log = runner, remote_port, bridge_info_fn, log
        self.proc = None
        self._stop = threading.Event()
        self._thread = None

    def start(self, local_port: int, token: str):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(local_port, token), name="cyph-tunnel", daemon=True)
        self._thread.start()

    def _run(self, local_port: int, token: str):
        delay = 2.0
        while not self._stop.is_set():
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                self.proc = subprocess.Popen(self.runner.tunnel_argv(self.remote_port, local_port),
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                time.sleep(3.0)                                      # give it a moment to connect
                if self.proc.poll() is None:
                    self.runner.bridge({"port": self.remote_port, "token": token})
                    self.log("info", "server link: the server can reach this Cyph (live arm and camera)")
                    delay = 2.0
                    self.proc.wait()
                    self.log("warn", "server link: tunnel dropped, reconnecting")
            except (SyncError, OSError) as e:
                self.log("warn", f"server link: {e}")
            self._stop.wait(delay)
            delay = min(60.0, delay * 2)

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.runner.unbridge()
        except SyncError:
            pass
