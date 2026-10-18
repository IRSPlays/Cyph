"""The "Hey Cyph" wake word, with openWakeWord: a tiny always-on keyword spotter (about 1 MB of model, a few ms
of CPU per 80 ms of audio). It does not transcribe anything, so room conversation is never turned into text and
nothing leaves the PC. Needs models/wake/{melspectrogram,embedding_model}.onnx and a trained models/wake/hey_cyph.onnx
(see docs/wake-word.md); without the trained model Cyph falls back to spotting the phrase in a local Whisper transcript.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np

WAKE_DIR = Path(__file__).resolve().parents[1] / "models" / "wake"
DEFAULT_MODEL = WAKE_DIR / "hey_cyph.onnx"
FRAME = 1280                      # 80 ms at 16 kHz: what openWakeWord wants per call


def available(model: Path | str | None = None) -> bool:
    m = Path(model or DEFAULT_MODEL)
    return (m.is_file() and (WAKE_DIR / "melspectrogram.onnx").is_file()
            and (WAKE_DIR / "embedding_model.onnx").is_file())


class WakeListener:
    """The mic and the spotter on their own thread, for when the voice session is OFF (no speech recognition, no
    brain, nothing recorded: just the keyword spotter). on_hit() is called from that thread when it hears the phrase."""

    def __init__(self, on_hit, mic, spotter, log=lambda *_: None):
        self.on_hit, self.mic, self.spotter, self.log = on_hit, mic, spotter, log
        self.running = False
        self._loop = None
        self._thread = None

    def start(self):
        import threading
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._main, name="cyph-wake", daemon=True)
        self._thread.start()

    def _main(self):
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        try:
            self.spotter.reset()
            self.mic.start(self._loop, q)
            self._loop.run_until_complete(self._read(q))
        except Exception as e:                                   # no mic, device busy ...
            self.log("warn", f"wake word: listener stopped ({type(e).__name__}: {e})")
        finally:
            try:
                self.mic.stop()
            except Exception:
                pass
            self.running = False
            self._loop.close()

    async def _read(self, q):
        while self.running:
            try:
                chunk = await asyncio.wait_for(q.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            if self.spotter.feed(np.frombuffer(chunk, dtype=np.int16)):
                self.running = False
                self.on_hit()

    def stop(self):
        self.running = False
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)


class WakeWord:
    def __init__(self, model: Path | str | None = None, threshold: float = 0.5, hits: int = 2,
                 cooldown_s: float = 2.0):
        from openwakeword.model import Model
        self.path = Path(model or DEFAULT_MODEL)
        self.threshold, self.need, self.cooldown_s = threshold, hits, cooldown_s
        self.model = Model(wakeword_models=[str(self.path)], inference_framework="onnx",
                           melspec_model_path=str(WAKE_DIR / "melspectrogram.onnx"),
                           embedding_model_path=str(WAKE_DIR / "embedding_model.onnx"))
        self.name = next(iter(self.model.models))
        self.buf = np.zeros(0, dtype=np.int16)
        self.run = 0
        self.quiet_until = 0.0
        self.last_score = 0.0

    def reset(self):
        self.buf = np.zeros(0, dtype=np.int16)
        self.run = 0
        self.model.reset()

    def feed(self, pcm16: np.ndarray) -> bool:
        """Audio in (int16, 16 kHz, any length). True when the wake word was just said."""
        self.buf = np.concatenate([self.buf, pcm16])
        hit = False
        while len(self.buf) >= FRAME:
            frame, self.buf = self.buf[:FRAME], self.buf[FRAME:]
            self.last_score = float(self.model.predict(frame)[self.name])
            # a couple of high frames in a row: a single spike from a click or a cough does not count
            self.run = self.run + 1 if self.last_score >= self.threshold else 0
            if self.run >= self.need and time.monotonic() >= self.quiet_until:
                hit = True
                self.run = 0
                self.quiet_until = time.monotonic() + self.cooldown_s
        return hit
