"""Microphone and speaker for the Live API.

Gemini wants 16 kHz 16-bit mono PCM in and sends 24 kHz 16-bit mono PCM
back. Both devices also expose a loudness `level` (0..1) so the body can
react locally: the speaker level drives the wrist bob and grabber flap, the
mic level drives the "listening" tilt. No model latency involved.
"""
from __future__ import annotations

import asyncio
import threading
import time

import numpy as np

IN_RATE = 16000
OUT_RATE = 24000


def _import_sd():
    import sounddevice as sd          # imported lazily so tests need no audio
    return sd


def list_devices() -> str:
    return str(_import_sd().query_devices())


# MME and DirectSound resample for us (16 kHz in / 24 kHz out); WASAPI shared
# mode and WDM-KS refuse rates the device does not run at, so they come last.
_API_ORDER = ("MME", "DirectSound", "WASAPI", "WDM-KS")


def resolve_device(spec, kind):
    """spec: None (system default), an int index, or a name fragment such as
    'Iriun'. kind: 'input' or 'output'. Returns (index or None, display name).
    Device numbers shift whenever something is plugged in, so names are safer."""
    sd = _import_sd()
    if spec is None or spec == "":
        return None, "system default"
    devs, apis = sd.query_devices(), sd.query_hostapis()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if isinstance(spec, int) or str(spec).strip().isdigit():
        i = int(spec)
        return i, f"#{i} {devs[i]['name']}"
    want = str(spec).lower()
    hits = []
    for i, d in enumerate(devs):
        if d[key] > 0 and want in d["name"].lower():
            api = apis[d["hostapi"]]["name"]
            rank = next((n for n, a in enumerate(_API_ORDER) if a in api), len(_API_ORDER))
            hits.append((rank, i, d["name"], api))
    if not hits:
        return None, f"system default ('{spec}' not found)"
    _, i, name, api = min(hits)
    return i, f"#{i} {name} [{api}]"


class Mic:
    def __init__(self, device=None):
        self.spec = device
        self.device = device
        self.label = "system default"
        self.level = 0.0
        self.peak = 0.0                 # loudest level since start (silence watchdog)
        self._stream = None

    def start(self, loop: asyncio.AbstractEventLoop, q: asyncio.Queue):
        sd = _import_sd()
        self.device, self.label = resolve_device(self.spec, "input")
        self.peak = 0.0

        def put(data):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass                     # drop rather than lag behind live

        def cb(indata, frames, t, status):
            x = indata[:, 0].astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x)))
            self.level = min(1.0, rms * 8.0)
            self.peak = max(self.peak, self.level)
            loop.call_soon_threadsafe(put, indata.tobytes())

        self._stream = sd.InputStream(
            samplerate=IN_RATE, channels=1, dtype="int16", blocksize=640,
            device=self.device, callback=cb)
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.level = 0.0


class Speaker:
    def __init__(self, device=None):
        self.spec = device
        self.device = device
        self.label = "system default"
        self.level = 0.0
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._last_audio = 0.0
        self._stream = None

    def start(self):
        sd = _import_sd()
        self.device, self.label = resolve_device(self.spec, "output")
        self._stream = sd.OutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16", blocksize=480,
            device=self.device, callback=self._cb)
        self._stream.start()

    def _cb(self, outdata, frames, t, status):
        need = frames * 2
        with self._lock:
            chunk = bytes(self._buf[:need])
            del self._buf[:need]
        if len(chunk) < need:
            chunk += b"\x00" * (need - len(chunk))
        samples = np.frombuffer(chunk, dtype=np.int16)
        outdata[:, 0] = samples
        rms = float(np.sqrt(np.mean((samples.astype(np.float32) / 32768.0) ** 2)))
        self.level = min(1.0, rms * 6.0)
        if rms > 0.002:
            self._last_audio = time.monotonic()

    def write(self, pcm: bytes):
        with self._lock:
            self._buf += pcm

    def clear(self):
        """Barge-in: drop everything queued so the user is not talked over."""
        with self._lock:
            self._buf.clear()

    @property
    def playing(self) -> bool:
        with self._lock:
            queued = len(self._buf) > 0
        return queued or (time.monotonic() - self._last_audio) < 0.25

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.level = 0.0


def mic_test(spec, seconds=6.0):
    """Print a live level meter for the chosen mic so you can see it hear you."""
    sd = _import_sd()
    idx, label = resolve_device(spec, "input")
    print(f"mic: {label}\nspeak now ({seconds:.0f} s)...")
    levels = []
    end = time.monotonic() + seconds

    def cb(indata, frames, t, status):
        x = indata[:, 0].astype(np.float32) / 32768.0
        lvl = min(1.0, float(np.sqrt(np.mean(x * x))) * 8.0)
        levels.append(lvl)
        n = int(lvl * 40)
        print("\r  [" + "#" * n + " " * (40 - n) + f"] {lvl:4.2f}", end="", flush=True)

    with sd.InputStream(samplerate=IN_RATE, channels=1, dtype="int16", blocksize=1600,
                        device=idx, callback=cb):
        while time.monotonic() < end:
            time.sleep(0.1)
    lv = sorted(levels) or [0.0]
    floor, loud = lv[len(lv) // 10], lv[int(len(lv) * 0.97)]
    print(f"\nnoise floor {floor:.3f}, loudest {loud:.3f}")
    if loud < 0.01:
        print("SILENT: wrong device, muted, or the Iriun phone app is not connected")
    elif loud < floor * 2 + 0.03:
        print("It only hears a steady background level: either you did not speak, or this is "
              "not the right mic. Speak while it runs.")
    else:
        print("OK, it hears you")
    return loud


class NullMic:
    """No microphone (used by the headless self-test)."""
    level = 0.0
    peak = 0.0
    label = "none"

    def start(self, loop, q):
        pass

    def stop(self):
        pass


class CountingSpeaker:
    """No speaker: just counts what Gemini sent (headless self-test)."""

    label = "counting"

    def __init__(self):
        self.bytes = 0
        self.level = 0.0
        self._last = 0.0

    def start(self):
        pass

    def write(self, pcm):
        self.bytes += len(pcm)
        self._last = time.monotonic()
        self.level = 0.5

    def clear(self):
        pass

    @property
    def playing(self):
        # pretend playback lasts as long as the audio would (24 kHz * 2 bytes)
        return (time.monotonic() - self._last) < 0.3

    def stop(self):
        pass
