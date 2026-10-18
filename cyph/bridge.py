"""The link between the desktop Cyph (arm, camera, voice) and the Telegram service.

- SharedChat: the last few messages of the conversation, in one small file both sides read and write, so
  talking on the phone and then at the desk (or the other way round) is one continuous conversation.
- BridgeServer: runs inside the desktop app while its session is live. It listens on 127.0.0.1 only and
  every request needs a random token that is written to data/bridge.json when it starts.
- ask_desktop(): used by the Telegram service. It returns the desktop session's reply, or None when the
  desktop app is not running (the service then answers by itself, text only).
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
INFO = DATA / "bridge.json"
SHARED = DATA / "user" / "recent_chat.json"
MAX_BODY = 14 * 1024 * 1024        # a few phone photos, base64


class SharedChat:
    def __init__(self, path=None, keep: int = 40, max_age_s: float = 3 * 3600):
        self.path, self.keep, self.max_age_s = Path(path) if path else SHARED, keep, max_age_s
        self.lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)]
        except (OSError, ValueError):
            return []

    def load(self, n: int = 12) -> list[dict]:
        """The last n messages that are recent enough to still be 'the same conversation'."""
        now = time.time()
        recent = [m for m in self._read() if now - float(m.get("t", 0)) <= self.max_age_s]
        return [{"role": m["role"], "content": m["content"]} for m in recent[-n:]]

    def last_time(self, role: str = "user") -> float:
        """When the newest message from that role was written (0 if none)."""
        for m in reversed(self._read()):
            if m["role"] == role:
                return float(m.get("t", 0))
        return 0.0

    def append(self, role: str, text: str, src: str = ""):
        text = " ".join((text or "").split())
        if not text:
            return
        with self.lock:
            msgs = self._read()
            msgs.append({"role": role, "content": text, "src": src, "t": time.time()})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(msgs[-self.keep:], ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)


class BridgeServer:
    """ask(text) -> reply runs on a worker thread of this server (it may take many seconds)."""

    def __init__(self, ask, info_path=None, log=lambda *_: None, timeout: float = 120.0):
        self.ask, self.log, self.timeout = ask, log, timeout
        self.info_path = Path(info_path) if info_path else INFO
        self.token = secrets.token_urlsafe(24)
        self.httpd = None

    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                got = self.headers.get("Authorization", "")
                return hmac.compare_digest(got.encode(), ("Bearer " + outer.token).encode())

            def do_GET(self):
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                self._send(200, {"ok": True}) if self.path == "/ping" else self._send(404, {"error": "no"})

            def do_POST(self):
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                if self.path != "/say":
                    return self._send(404, {"error": "no"})
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if not 0 < n <= MAX_BODY:
                        return self._send(413, {"error": "bad size"})
                    body = json.loads(self.rfile.read(n))
                    text = str(body.get("text", "")).strip()
                    images = [{"mime": str(i["mime"]), "data": base64.b64decode(i["b64"])}
                              for i in body.get("images") or []][:4]
                except (ValueError, TypeError, KeyError):
                    return self._send(400, {"error": "bad request"})
                if not text:
                    return self._send(400, {"error": "empty"})
                try:
                    self._send(200, {"reply": outer.ask(text, images) if images else outer.ask(text)})
                except Exception as e:
                    self._send(503, {"error": f"{type(e).__name__}: {e}"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.info_path.parent.mkdir(parents=True, exist_ok=True)
        self.info_path.write_text(json.dumps({"port": port, "token": self.token, "pid": os.getpid()}),
                                  encoding="utf-8")
        threading.Thread(target=self.httpd.serve_forever, name="cyph-bridge", daemon=True).start()
        self.log("info", f"phone bridge: listening on 127.0.0.1:{port} (Telegram messages reach this session)")
        return port

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        try:
            if json.loads(self.info_path.read_text(encoding="utf-8")).get("token") == self.token:
                self.info_path.unlink()
        except (OSError, ValueError):
            pass


def desktop_info(info_path=None) -> dict | None:
    try:
        d = json.loads(Path(info_path or INFO).read_text(encoding="utf-8"))
        return d if d.get("port") and d.get("token") else None
    except (OSError, ValueError):
        return None


def _call(info: dict, method: str, path: str, timeout: float, **kw):
    import httpx
    return httpx.request(method, f"http://127.0.0.1:{info['port']}{path}", timeout=timeout,
                         headers={"Authorization": "Bearer " + info["token"]}, **kw)


def desktop_alive(info_path=None) -> bool:
    info = desktop_info(info_path)
    if not info:
        return False
    try:
        return _call(info, "GET", "/ping", 1.5).status_code == 200
    except Exception:
        return False


def ask_desktop(text: str, images=None, info_path=None, timeout: float = 130.0) -> str | None:
    """The desktop session's reply, or None if the desktop app is not running / not live.
    images: [{"mime", "data": bytes}] to show it along with the message."""
    info = desktop_info(info_path)
    if not info:
        return None
    try:
        payload = {"text": text}
        if images:
            payload["images"] = [{"mime": i["mime"], "b64": base64.b64encode(i["data"]).decode()} for i in images]
        r = _call(info, "POST", "/say", timeout, json=payload)
    except Exception as e:
        import httpx
        if isinstance(e, httpx.ReadTimeout):
            raise TimeoutError("the desktop Cyph did not answer in time")
        return None
    if r.status_code == 503:
        return None                                  # running but its brain is not live
    if r.status_code != 200:
        return None
    return str(r.json().get("reply", ""))
