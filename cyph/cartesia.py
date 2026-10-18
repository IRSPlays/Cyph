"""Cartesia Ink-2 speech-to-text for the local brain.

Audio is streamed to Cartesia's realtime endpoint while Haziq is still talking (our Silero VAD decides
when speech starts and stops), and "finalize" is sent the moment it stops, so the transcript is back about
one network round trip after the last word. Docs: https://docs.cartesia.ai/build-with-cartesia/stt/latest

- The API key is read from CARTESIA_API_KEY or the untracked cyph/.env, never written anywhere or logged.
- If Cartesia is unreachable or errors, that utterance falls back to the local Whisper model.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from concurrent.futures import Future
from pathlib import Path
from urllib.parse import quote

import numpy as np

URL = "wss://api.cartesia.ai/stt/websocket"
API_VERSION = "2026-08-14"
MODEL = "ink-2"
KEYTERMS = ("Cyph", "Cypher", "VectorM", "Cipher")        # names it should get right
SAMPLE_RATE = 16000
FINALIZE_TIMEOUT_S = 6.0

KEY_SOURCE = ""


def load_cartesia_key() -> str | None:
    """CARTESIA_API_KEY from the environment, then cyph/.env."""
    global KEY_SOURCE
    key = os.environ.get("CARTESIA_API_KEY")
    if key and key.strip():
        KEY_SOURCE = "CARTESIA_API_KEY environment variable"
        return key.strip()
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("CARTESIA_API_KEY="):
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val:
                    KEY_SOURCE = "cyph/.env"
                    return val
    return None


def ws_url(base: str = URL, model: str = MODEL) -> str:
    q = f"model={model}&encoding=pcm_s16le&sample_rate={SAMPLE_RATE}&cartesia_version={API_VERSION}"
    for k in KEYTERMS:
        q += "&keyterm=" + quote(k)
    return base + "?" + q


class CartesiaError(RuntimeError):
    pass


class _Stream:
    """One utterance: feed() audio as it arrives, finish() asks for the transcript (a Future[str])."""

    _FINALIZE = object()
    _CANCEL = object()

    def __init__(self, stt: "CartesiaStt"):
        self.stt = stt
        self.q: queue.Queue = queue.Queue()
        self.result: Future = Future()
        self.pcm: list[np.ndarray] = []          # kept so a failure can still be transcribed locally
        threading.Thread(target=self._run, name="cyph-cartesia", daemon=True).start()

    def feed(self, pcm16: np.ndarray):
        self.pcm.append(pcm16)
        self.q.put(pcm16.astype("<i2").tobytes())

    def finish(self) -> Future:
        self.q.put(self._FINALIZE)
        return self.result

    def cancel(self):
        self.q.put(self._CANCEL)

    def _run(self):
        from websockets.sync.client import connect
        try:
            ws = connect(self.stt.url, additional_headers={"X-API-Key": self.stt.key},
                         open_timeout=5, close_timeout=1, max_size=2 ** 20)
        except Exception as e:
            self._fail(e)
            return
        try:
            with ws:
                while True:
                    item = self.q.get()
                    if item is self._CANCEL:
                        self.result.cancel()
                        return
                    if item is self._FINALIZE:
                        break
                    ws.send(item)
                ws.send("finalize")
                text = ""
                while True:
                    msg = ws.recv(timeout=FINALIZE_TIMEOUT_S)
                    if isinstance(msg, bytes):
                        continue
                    d = json.loads(msg)
                    kind = d.get("type")
                    if kind == "transcript":
                        if d.get("is_final", True):
                            text += d.get("text", "")        # deltas: never strip or re-space them
                    elif kind == "flush_done":
                        break
                    elif kind == "error":
                        raise CartesiaError(f"{d.get('status_code')} {d.get('title')}: {d.get('message')}")
                try:
                    ws.send("close")
                except Exception:
                    pass
                self.result.set_result(" ".join(text.split()))
        except Exception as e:
            self._fail(e)

    def _fail(self, e: Exception):
        if not self.result.done():
            self.result.set_exception(e if isinstance(e, CartesiaError) else CartesiaError(
                f"{type(e).__name__}: {e}"))


class CartesiaStt:
    """Same transcribe(pcm16) -> str as the Whisper Stt, plus open() for streaming while the user talks."""

    streaming = True

    def __init__(self, key: str, fallback=None, url: str | None = None, model: str = MODEL, log=None):
        if not key:
            raise CartesiaError("no Cartesia key")
        self.key = key
        self.url = url or ws_url(model=model)
        self.fallback = fallback                     # callable returning a Whisper-style Stt, or None
        self.log = log or (lambda lvl, msg: None)
        self._warned = False

    def open(self) -> _Stream:
        return _Stream(self)

    def transcribe(self, pcm16: np.ndarray) -> str:
        """Whole utterance at once (the non-streaming path): used by tests and for the fallback."""
        s = self.open()
        for i in range(0, len(pcm16), 3200):
            s.feed(pcm16[i:i + 3200])
        return self.resolve(s.finish(), pcm16)

    def resolve(self, fut: Future, pcm16: np.ndarray) -> str:
        """The transcript from a finished stream, or the local model's if Cartesia failed."""
        try:
            return fut.result(timeout=FINALIZE_TIMEOUT_S + 3)
        except Exception as e:
            if not self._warned:
                self._warned = True
                self.log("warn", f"cartesia stt failed ({e}); using local Whisper for this and until it recovers")
            fb = self.fallback() if self.fallback else None
            return fb.transcribe(pcm16) if fb else ""
