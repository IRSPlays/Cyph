"""Voice for the PC agent: hear him, think, answer out loud.

    mic -> Silero VAD -> Cartesia Ink-2 (streamed while he talks) -> the server brain over /chat
        -> sentence chunker -> Cartesia Sonic (streamed) -> speaker

It lives in the arm agent's process because that is the one process on the PC that already owns the arm, the camera and the
speaker, and runs whenever the app does. Everything he says and hears goes through the same server chat as the app and
Telegram, so every device shows the conversation, and the server's memory and tools work by voice too.

Behaviour is the one the terminal Cyph had: VAD decides when he starts and stops talking, talking over Cyph interrupts him
(barge-in), after a few quiet minutes he sleeps until "Hey Cyph" (spotted locally: while asleep nothing leaves the PC), and a
held key (push to talk) works whatever state he is in. The Cartesia key is read the way cartesia.py reads it (env or the
untracked cyph/.env) and is never logged. If Cartesia fails, speech-to-text falls back to local Whisper and text-to-speech
to the local Supertonic voice, with one warning.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .cartesia import API_VERSION, CartesiaStt, load_cartesia_key
from .voice_cfg import BadSetting, DEFAULTS, VOICES, check_change, clean_stored

CFG_FILE = Path(__file__).resolve().parents[1] / ".cyph_voice.json"
TTS_URL = f"wss://api.cartesia.ai/tts/websocket?cartesia_version={API_VERSION}"
TTS_MODEL = "sonic-latest"
OUT_RATE = 24000
SAMPLE_16K = 16000
WAKE_REPLY = "[Cypher called you by saying 'Hey Cyph'. Answer in a few words, like 'yeah?'.]"
SEES = re.compile(r"\b(see|look|looking|show|showing|holding|wearing|camera|picture|this|that)\b", re.I)
KNOWN_MOODS = None            # filled from the body's expression list on first use


# ---- small pure helpers (tested on their own) ---------------------------------------------------------------------
class MoodTag:
    """The reply starts with one [mood] tag. feed() returns the text with the tag taken out and sets .mood when it is seen."""

    def __init__(self):
        self.mood = None
        self.done = False
        self.head = ""

    def feed(self, text: str) -> str:
        if self.done:
            return text
        self.head += text
        s = self.head.lstrip()
        if not s:
            return ""
        if s[0] != "[":
            self.done, out, self.head = True, self.head, ""
            return out
        end = s.find("]")
        if end == -1:
            if len(s) > 24:                                  # no closing bracket: it was not a tag
                self.done, out, self.head = True, self.head, ""
                return out
            return ""
        self.mood = s[1:end].strip().lower() or None
        self.done, self.head = True, ""
        return s[end + 1:].lstrip()


_TAG = re.compile(r"\[[A-Za-z ]{2,16}\]")


def speakable(text: str) -> str:
    """What the voice should read: no mood tags, markdown, emoji or code (and nothing when no word is left)."""
    from .local import clean_for_tts
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1), text)        # [label](url) -> label, before tags go
    t = clean_for_tts(_TAG.sub(" ", text))
    return t if re.search(r"\w", t) else ""


# ---- the chat link to the server ---------------------------------------------------------------------------------
class _Resp:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.dead = False


class ChatLink:
    """The agent's own /chat socket. Each `say` gets exactly one response sequence (busy, or events then final/error), in
    order, so responses are matched to says by a queue instead of guessing: an abandoned turn is simply dropped when it
    ends and never leaks into the next one."""

    def __init__(self, host, fp, token, log=print):
        self.host, self.fp, self.token, self.log = host, fp, token, log
        self.ws = None
        self.up = asyncio.Event()
        self._fifo: deque[_Resp] = deque()

    async def run(self):
        import websockets
        from .arm_agent import pinned_context
        delay = 1.0
        while True:
            try:
                ctx = await asyncio.get_running_loop().run_in_executor(None, pinned_context, self.host, self.fp)
                async with websockets.connect(f"wss://{self.host}/chat", ssl=ctx, ping_interval=20, max_size=2 ** 20) as ws:
                    await ws.send(json.dumps({"type": "auth", "token": self.token}))
                    hello = json.loads(await ws.recv())
                    if hello.get("type") != "ready":
                        raise ConnectionError("not accepted")
                    self.ws, delay = ws, 1.0
                    self.up.set()
                    async for raw in ws:
                        try:
                            self._dispatch(json.loads(raw))
                        except ValueError:
                            pass
            except SystemExit:
                raise
            except Exception as e:
                if getattr(getattr(e, "rcvd", None), "code", None) == 4401:
                    self.log("error", "voice: the server no longer accepts this device's token; pair the agent again")
                    return
            finally:
                self.ws = None
                self.up.clear()
                self._fail_all()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)

    def _dispatch(self, ev: dict):
        if not self._fifo:
            return
        head, kind = self._fifo[0], ev.get("type")
        if kind in ("busy", "final", "error"):
            self._fifo.popleft()
        elif kind not in ("delta", "event"):
            return
        if not head.dead:
            head.q.put_nowait(ev)

    def _fail_all(self):
        while self._fifo:
            r = self._fifo.popleft()
            if not r.dead:
                r.q.put_nowait({"type": "error", "message": "the link to the server is down"})

    async def ask(self, text: str, images=None, patience_s=30.0):
        """Events of one turn ({"type":"delta"|"event"|"final"|"error"}). If the server is still busy with another turn
        (his last one, cut short), wait for it instead of failing."""
        end = time.monotonic() + patience_s
        while True:
            try:
                await asyncio.wait_for(self.up.wait(), 5.0)
            except asyncio.TimeoutError:
                yield {"type": "error", "message": "the server is not reachable"}
                return
            r = _Resp()
            self._fifo.append(r)
            frame = {"type": "say", "text": text[:4000]}
            if images:
                frame["images"] = images
            try:
                await self.ws.send(json.dumps(frame))
                while True:
                    ev = await r.q.get()
                    if ev["type"] == "busy":
                        break
                    yield ev
                    if ev["type"] in ("final", "error"):
                        return
            finally:
                r.dead = True                                    # abandoned (barge-in) or finished: later events are dropped
            if time.monotonic() > end:
                yield {"type": "error", "message": "the server stayed busy"}
                return
            await asyncio.sleep(0.6)


# ---- Cartesia Sonic -----------------------------------------------------------------------------------------------
class CartesiaError(RuntimeError):
    pass


class TtsReply:
    """One spoken reply: a single Sonic context, fed a sentence at a time while the brain is still writing. Audio goes to
    `on_audio(pcm16_bytes_24k)` as it arrives. One sentence is always held back so the last one can say "that is all"
    (continue=false), which is what lets the voice finish the sentence properly."""

    def __init__(self, key: str, voice: str, speed: float, on_audio):
        self.key, self.voice, self.speed, self.on_audio = key, voice, speed, on_audio
        self.ctx = uuid.uuid4().hex
        self.ws = None
        self.held: str | None = None
        self.sent = False
        self._reader = None
        self._err: Exception | None = None

    async def open(self):
        from websockets.asyncio.client import connect
        self.ws = await connect(TTS_URL, additional_headers={"X-API-Key": self.key}, open_timeout=6, max_size=2 ** 22)
        self._reader = asyncio.create_task(self._read())

    async def say(self, text: str):
        if self.held is not None:
            await self._send(self.held, True)
        self.held = text

    async def finish(self):
        if self.held is not None:
            await self._send(self.held, False)
            self.held = None
        if self._reader is not None and self.sent:
            await asyncio.wait_for(self._reader, 40)
        if self._err:
            raise self._err

    async def cancel(self):
        r, ws = self._reader, self.ws
        self.held = None
        if ws is not None:
            try:
                await ws.send(json.dumps({"context_id": self.ctx, "cancel": True}))
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
        if r is not None:
            r.cancel()
            await asyncio.gather(r, return_exceptions=True)

    async def _send(self, text: str, more: bool):
        if self.ws is None:
            await self.open()
        self.sent = True
        await self.ws.send(json.dumps({
            "model_id": TTS_MODEL, "transcript": text + " ", "voice": {"mode": "id", "id": self.voice}, "language": "en",
            "context_id": self.ctx, "continue": more, "max_buffer_delay_ms": 0,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": OUT_RATE},
            "generation_config": {"speed": self.speed}}))

    async def _read(self):
        try:
            async for raw in self.ws:
                d = json.loads(raw)
                kind = d.get("type")
                if kind == "chunk" and d.get("data"):
                    self.on_audio(base64.b64decode(d["data"]))
                elif kind == "done":
                    return
                elif kind == "error":
                    raise CartesiaError(f"{d.get('status_code')} {d.get('title')}: {d.get('message')}")
        except Exception as e:
            self._err = e if isinstance(e, CartesiaError) else CartesiaError(f"{type(e).__name__}: {e}")
        finally:
            if self.ws is not None:
                try:
                    await self.ws.close()
                except Exception:
                    pass


# ---- the engine -----------------------------------------------------------------------------------------------------
class VoiceEngine:
    BARGE_PROB = 0.85          # as in the terminal Cyph: clear, sustained, louder than his own voice, never in the first moments
    BARGE_FRAMES = 8
    BARGE_MIN_RMS = 700
    BARGE_ECHO_X = 2.0
    BARGE_GRACE_S = 0.5
    PTT_MAX_S = 25.0           # a key that is never released (a lost message) cannot hold the mic open for ever
    MAX_UTTER_S = 20.0

    def __init__(self, body, chat, camera=None, log=print, cfg_path=CFG_FILE, mic=None, speaker=None, stt=None, vad=None,
                 key=None, whisper_path=None):
        self.body, self.chat, self.camera, self._log_fn, self.cfg_path = body, chat, camera, log, Path(cfg_path)
        self.cfg = self._load_cfg()
        self.key = key if key is not None else load_cartesia_key()
        self.mic, self.speaker, self.stt, self.vad = mic, speaker, stt, vad
        self.whisper_path = whisper_path
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(2, thread_name_prefix="cyph-stt")
        self.asleep = False
        self.hearing = False
        self.thinking = False
        self.speaking = False
        self.level = 0.0
        self.err = ""
        self.tts_name = "cartesia sonic" if self.key else "local"
        self.stt_name = "cartesia ink-2" if self.key else "local whisper"
        self._ptt_until = 0.0
        self._ptt_was = False
        self._active_at = time.monotonic()
        self._turn_task: asyncio.Task | None = None
        self._reply: TtsReply | None = None
        self._warned: set[str] = set()
        self._wake_det = None
        self._wake_tried = False
        self._wake_stt = None
        self._local_tts = None
        self._mic_on = False
        self._info_dirty = True
        self._cfg_seq = 0
        self.speaker_ok = True

    # ---- settings ----------------------------------------------------------------------------------------------
    def _load_cfg(self) -> dict:
        try:
            return clean_stored(json.loads(self.cfg_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return dict(DEFAULTS)

    def _save_cfg(self):
        try:
            tmp = self.cfg_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cfg, indent=1), encoding="utf-8")
            tmp.replace(self.cfg_path)
        except OSError as e:
            self.log("warn", f"voice: could not save settings ({e})")

    def log(self, level, text):
        self._log_fn(level, text)

    def warn_once(self, key, text):
        if key not in self._warned:
            self._warned.add(key)
            self.log("warn", text)

    # ---- thread-safe surface for the agent -----------------------------------------------------------------------
    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), name="cyph-voice", daemon=True)
        self._thread.start()

    def say(self, text: str):
        """Speak a line (from any thread), in the chosen voice. Dropped when voice is not running."""
        loop = self._loop
        if loop is not None and getattr(self, "_turns", None) is not None and str(text).strip():
            loop.call_soon_threadsafe(self._turns.put_nowait, ("say", str(text)[:300]))

    def snapshot(self, first=False) -> dict:
        if not self.cfg["on"] and not self._ptt_active():
            state = "off"
        elif self.asleep:
            state = "asleep"
        elif self.speaking:
            state = "speaking"
        elif self.thinking:
            state = "thinking"
        elif self.hearing:
            state = "hearing"
        else:
            state = "idle"
        s = {"state": state, "on": self.cfg["on"], "mode": self.cfg["mode"], "asleep": self.asleep, "level": round(self.level, 2),
             "stt": self.stt_name, "tts": self.tts_name, "cfg": dict(self.cfg), "ptt": self._ptt_active()}
        if self.err:
            s["err"] = self.err
        if first or self._info_dirty:
            s["voices"] = [{"id": i, "name": n} for i, n in VOICES]
            s["mic"] = getattr(self.mic, "label", "")
            s["speaker"] = getattr(self.speaker, "label", "")
            try:
                s["devices"] = self._device_names()
            except Exception:
                pass
            self._info_dirty = False
        return s

    def command(self, c: dict):
        """Called from the agent's loop: applies a command that the server already checked. Returns a short log line."""
        kind = c.get("cmd")
        loop = self._loop
        if loop is None:
            raise ValueError("voice is not running")
        if kind == "voice":
            loop.call_soon_threadsafe(self._set_on, bool(c["on"]))
            return "voice " + ("on" if c["on"] else "off")
        if kind == "ptt":
            loop.call_soon_threadsafe(self._ptt, bool(c["on"]))
            return "talk key " + ("down" if c["on"] else "up")
        if kind == "voice_cfg":
            change = check_change(c.get("set"))
            loop.call_soon_threadsafe(self._apply_cfg, change)
            return "voice settings " + ", ".join(change)
        if kind == "voice_test":
            loop.call_soon_threadsafe(self._turns.put_nowait, ("say", "This is how I sound. Hello, Haziq!"))
            return "voice test"
        if kind == "sleep":
            loop.call_soon_threadsafe(self.sleep)
            return "sleep"
        if kind == "wake":
            loop.call_soon_threadsafe(lambda: self.wake("asked", quiet=True))    # the agent wakes the body itself
            return None
        raise ValueError("unknown voice command")

    # ---- setup -----------------------------------------------------------------------------------------------------
    def _device_names(self) -> dict:
        import sounddevice as sd
        mics, outs = [], []
        for d in sd.query_devices():
            n = d["name"]
            if d["max_input_channels"] > 0 and n not in mics:
                mics.append(n)
            if d["max_output_channels"] > 0 and n not in outs:
                outs.append(n)
        return {"mics": mics[:24], "speakers": outs[:24]}

    def _build(self):
        from . import audio
        from .local import LocalConfig, Stt, Vad
        if self.mic is None:
            self.mic = audio.Mic(self.cfg["mic"] or None)
        if self.speaker is None:
            self.speaker = audio.Speaker(self.cfg["speaker"] or None)
        if self.vad is None:
            self.vad = Vad()
        wp = self.whisper_path or LocalConfig().whisper
        self.whisper_path = wp
        if self.stt is None and self.key:
            self.stt = CartesiaStt(self.key, fallback=lambda: self._whisper(), log=self.log)
        elif self.stt is None:
            self.stt = _WhisperAsStt(self._whisper)

    def _whisper(self):
        from .local import Stt
        if self._wake_stt is None:
            self._wake_stt = Stt(self.whisper_path)
        return self._wake_stt

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._turns: asyncio.Queue = asyncio.Queue()
        self._mic_q: asyncio.Queue = asyncio.Queue(200)
        try:
            await self._loop.run_in_executor(None, self._build)
        except Exception as e:
            self.err = f"voice could not start: {type(e).__name__}: {e}"
            self.log("error", self.err)
            return
        self.speaker_ok = True
        try:
            self.speaker.start()
        except Exception as e:
            self.speaker_ok = False
            self.err = "no speaker"
            self.log("warn", f"voice: speaker unavailable ({type(e).__name__}: {e})")
        if not self.key:
            self.warn_once("key", "voice: no Cartesia key (CARTESIA_API_KEY or cyph/.env): using local Whisper and the local voice")
        self._mic_sync()
        self.log("info", f"voice: mic {getattr(self.mic, 'label', '?')}, speaker {getattr(self.speaker, 'label', '?')}, "
                         f"stt {self.stt_name}, tts {self.tts_name}")
        coros = [self._listen(), self._converse(), self._tick()]
        if hasattr(self.chat, "run"):
            coros.append(self.chat.run())                              # the /chat socket lives in this thread's loop
        tasks = [asyncio.create_task(t) for t in coros]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self._mic_stop()

    def _mic_sync(self):
        """The mic stream is open exactly while voice is on or a talk key is down (so the OS mic indicator is honest)."""
        want = self.cfg["on"] or self._ptt_active()
        if want and not self._mic_on:
            try:
                self.mic.start(self._loop, self._mic_q)
                self._mic_on = True
                self.err = "" if self.err == "no microphone" else self.err
            except Exception as e:
                self.err = "no microphone"
                self.warn_once("mic", f"voice: cannot open the microphone ({type(e).__name__}: {e})")
        elif not want and self._mic_on:
            self._mic_stop()

    def _mic_stop(self):
        if self._mic_on:
            try:
                self.mic.stop()
            except Exception:
                pass
        self._mic_on = False
        self.level = 0.0

    # ---- control ---------------------------------------------------------------------------------------------------
    def _set_on(self, on: bool):
        self.cfg["on"] = on
        self._save_cfg()
        if not on:
            self.hearing = False
            self.body.set_listening(False, quiet=True)
        self._mic_sync()

    def _ptt(self, down: bool):
        self._ptt_until = time.monotonic() + self.PTT_MAX_S if down else 0.0
        if down:
            self.wake("talk key", quiet=True)
        self._mic_sync()
        if not down:
            try:
                self._mic_q.put_nowait(b"")             # wakes the listener to end the utterance (the mic may be closing now)
            except asyncio.QueueFull:
                pass

    def _ptt_active(self) -> bool:
        return time.monotonic() < self._ptt_until

    def _apply_cfg(self, change: dict):
        self.cfg.update(change)
        self._save_cfg()
        self._info_dirty = True
        if "on" in change:
            self._set_on(change["on"])
        if "mic" in change or "speaker" in change:
            self._restart_devices(("mic" in change), ("speaker" in change))
        if "mode" in change and change["mode"] == "ptt":
            self.wake("mode", quiet=True)               # hold-to-talk has no sleeping

    def _restart_devices(self, mic: bool, spk: bool):
        from . import audio
        try:
            if mic:
                self._mic_stop()
                self.mic = audio.Mic(self.cfg["mic"] or None)
                self._mic_sync()
            if spk:
                self.speaker.stop()
                self.speaker = audio.Speaker(self.cfg["speaker"] or None)
                self.speaker.start()
        except Exception as e:
            self.err = "device change failed"
            self.log("warn", f"voice: device change failed ({type(e).__name__}: {e})")

    def sleep(self):
        if self.asleep or self.cfg["mode"] == "ptt":
            return
        self.asleep = True
        det = self._wake_detector()
        if det is not None:
            det.reset()
        self.body.set_calm(True)
        self.body.express("sleepy")
        self.log("info", "voice: asleep. Say 'Hey Cyph', hold the talk key, or type to wake him")

    def wake(self, why="asked", quiet=False):
        self._active_at = time.monotonic()
        if not self.asleep:
            return
        self.asleep = False
        self.body.set_calm(False)
        if not quiet:
            self.body.wake()
        self.log("info", f"voice: awake ({why})")

    def _wake_detector(self):
        if self._wake_det is None and not self._wake_tried:
            self._wake_tried = True
            from . import wake as wakeword
            if wakeword.available(None):
                try:
                    self._wake_det = wakeword.WakeWord(None, threshold=0.5)
                    self.log("info", "voice: wake word by openWakeWord, nothing is transcribed while asleep")
                except Exception as e:
                    self.log("warn", f"voice: openWakeWord failed ({type(e).__name__}: {e}); local Whisper listens for it instead")
        return self._wake_det

    # ---- hearing ---------------------------------------------------------------------------------------------------
    def _frame_size(self):
        from .local import FRAME
        return FRAME

    async def _listen(self):
        from .local import FRAME
        loop = asyncio.get_running_loop()
        buf = np.zeros(0, dtype=np.int16)
        pre: list[np.ndarray] = []
        utter: list[np.ndarray] = []
        talking, hot, quiet, voiced = False, 0, 0, 0
        stream = None
        ptt_talking = False
        frame_s = FRAME / SAMPLE_16K
        echo, speak_t0, last_speaking, was_speaking = 0.0, 0.0, -9.0, False
        while True:
            chunk = await self._mic_q.get()
            now = time.monotonic()
            data = np.frombuffer(chunk, dtype=np.int16)
            self.level = float(min(1.0, np.sqrt(np.mean((data.astype(np.float32) / 32768.0) ** 2)) * 8.0)) if len(data) else 0.0
            if self.body.sfx_busy():
                continue
            # ---- push to talk: the key defines the utterance, no VAD ------------------------------------------
            if self._ptt_active():
                if not ptt_talking:
                    ptt_talking, utter = True, list(pre)
                    self._cancel_turn()
                    self._set_hearing(True)
                    stream = self._open_stream()
                    for f in utter:
                        stream.feed(f) if stream else None
                utter.append(data)
                if stream is not None:
                    stream.feed(data)
                continue
            if ptt_talking:
                ptt_talking = False
                self._set_hearing(False)
                await self._finish_utterance(utter, stream)
                utter, stream, buf, hot, quiet = [], None, np.zeros(0, dtype=np.int16), 0, 0
                continue
            if self.cfg["mode"] == "ptt" or not self.cfg["on"]:
                continue
            # ---- hands-free ------------------------------------------------------------------------------------
            if self.asleep and self._wake_detector() is not None:
                if await loop.run_in_executor(None, self._wake_det.feed, data):
                    await self._turns.put(("wake", None))
                continue
            speaking = self.speaking or self.speaker.playing
            if speaking and not was_speaking and now - last_speaking > 0.8:
                speak_t0 = now
            if speaking:
                last_speaking = now
            was_speaking = speaking
            barge = self.cfg["barge_in"]
            if speaking and not barge:
                continue                                           # no headphones: do not hear ourselves
            buf = np.concatenate([buf, data])
            end_frames = max(1, int(self.cfg["end_silence"] / frame_s))
            while len(buf) >= FRAME:
                frame, buf = buf[:FRAME], buf[FRAME:]
                prob = await loop.run_in_executor(None, self.vad, frame)
                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                if speaking and not talking and prob < 0.5:
                    echo += (rms - echo) * 0.1                      # learn how loud his own voice bleeds into the mic
                if speaking:
                    ok = (prob > self.BARGE_PROB and now - speak_t0 > self.BARGE_GRACE_S
                          and rms > max(self.BARGE_MIN_RMS, self.BARGE_ECHO_X * echo))
                    need = self.BARGE_FRAMES
                else:
                    ok, need = prob > 0.5, 2
                if not talking:
                    pre.append(frame)
                    del pre[:-10]
                    hot = hot + 1 if ok else 0
                    if hot >= need:
                        talking, quiet, voiced, utter = True, 0, hot, list(pre)
                        self._set_hearing(True)
                        if not self.asleep:
                            stream = self._open_stream()
                            for f in utter:
                                stream.feed(f) if stream else None
                        if speaking:
                            self.log("info", "voice: barge-in")
                            self._cancel_turn()
                else:
                    utter.append(frame)
                    if stream is not None:
                        stream.feed(frame)
                    voiced += 1 if prob > 0.5 else 0
                    quiet = quiet + 1 if prob < 0.35 else 0
                    if quiet >= end_frames or len(utter) * frame_s > self.MAX_UTTER_S:
                        talking, hot = False, 0
                        self._set_hearing(False)
                        self.vad.reset()
                        done, utter, s2, stream = utter, [], stream, None
                        await self._finish_utterance(done, s2, voiced * frame_s)

    def _open_stream(self):
        """A Cartesia stream for the utterance being spoken (None when asleep or when speech-to-text is local)."""
        if self.asleep or not getattr(self.stt, "streaming", False):
            return None
        try:
            return self.stt.open()
        except Exception as e:
            self.warn_once("stt-open", f"voice: could not open speech-to-text ({e})")
            return None

    async def _finish_utterance(self, frames, stream, voiced_s=9.0):
        """voiced_s: how much of it was really speech (a click or a cough with a pause around it is not an utterance)."""
        pcm = np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)
        if len(pcm) <= SAMPLE_16K * 0.3 or voiced_s < 0.25:
            if stream is not None:
                stream.cancel()
            return
        fut = None
        if stream is not None:
            fut = self._loop.run_in_executor(self._pool, self.stt.resolve, stream.finish(), pcm)
        await self._turns.put(("speech", (pcm, fut)))

    def _set_hearing(self, on: bool):
        self.hearing = on
        self.body.set_listening(on, quiet=True)

    # ---- thinking and speaking --------------------------------------------------------------------------------------
    def _cancel_turn(self):
        t = self._turn_task
        if t is not None and not t.done():
            t.cancel()
        self.speaker.clear()

    async def _converse(self):
        loop = asyncio.get_running_loop()
        while True:
            kind, payload = await self._turns.get()
            if kind == "speech" and self.asleep:
                pcm, _ = payload
                text = await loop.run_in_executor(self._pool, self._wake_check, pcm)
                if text is None:
                    continue                                          # not for him: never kept, never sent anywhere
                self.wake("wake word")
                text = text or WAKE_REPLY
            elif kind == "speech":
                self._cancel_turn()
                pcm, fut = payload
                try:
                    text = await (fut or loop.run_in_executor(self._pool, self.stt.transcribe, pcm))
                except Exception as e:
                    self.warn_once("stt", f"voice: could not understand that ({type(e).__name__})")
                    continue
                if not text:
                    continue
                self.wake("heard you", quiet=True)
            elif kind == "wake":
                if not self.asleep:
                    continue
                self.wake("wake word")
                text = WAKE_REPLY
            elif kind == "say":                                    # a line to speak as is (the voice test)
                self._cancel_turn()
                self._turn_task = asyncio.create_task(self._speak_plain(payload))
                continue
            else:
                continue
            self._active_at = time.monotonic()
            self.body.converse()
            if self._turn_task is not None and not self._turn_task.done():
                self._turn_task.cancel()
                await asyncio.gather(self._turn_task, return_exceptions=True)
            self._turn_task = asyncio.create_task(self._respond(text))

    def _wake_check(self, pcm):
        """Asleep: local Whisper only. None if "Hey Cyph" was not said, else whatever followed it."""
        from .local import LocalSession
        try:
            heard = self._whisper().transcribe(pcm)
        except Exception as e:
            self.warn_once("wake", f"voice: wake word check failed ({type(e).__name__}: {e})")
            return None
        m = LocalSession.WAKE.search(heard or "")
        return None if not m else heard[m.end():].strip()

    def _images_for(self, text: str):
        cam = self.camera
        if cam is None or getattr(cam, "state", "") != "live" or not SEES.search(text):
            return None
        data = cam.jpeg(max_w=640, quality=70)
        return [{"mime": "image/jpeg", "data": base64.b64encode(data).decode()}] if data else None

    def _play(self, pcm: bytes):
        if not self.speaker_ok:
            return                                                    # nothing would ever drain the buffer
        vol = float(self.cfg["volume"])
        if vol < 0.995:
            pcm = (np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * vol).astype(np.int16).tobytes()
        self.speaker.write(pcm)

    def _new_reply(self) -> TtsReply | None:
        if not self.key:
            return None
        return TtsReply(self.key, self.cfg["voice"], float(self.cfg["speed"]), self._play)

    async def _speak_plain(self, text: str):
        sp = _Speech(self)
        try:
            await sp.say(text, last=True)
            await sp.close()
        except asyncio.CancelledError:
            await sp.abort()
            raise

    async def _respond(self, text: str):
        from .local import Chunker
        sp = _Speech(self)
        tag, chunker = MoodTag(), Chunker()
        got, final_text = False, ""
        self.thinking = True
        try:
            async for ev in self.chat.ask(text, self._images_for(text)):
                kind = ev["type"]
                if kind == "delta":
                    got = True
                    clean = tag.feed(ev["text"])
                    if tag.mood and tag.mood != getattr(sp, "mood", None):
                        sp.mood = tag.mood
                        self._mood(tag.mood)
                    for piece in chunker.feed(clean):
                        await sp.say(piece)
                elif kind == "final":
                    final_text = ev.get("text", "")
                elif kind == "error":
                    self.log("warn", f"voice: {ev.get('message', 'the reply failed')}")
                    if not got:
                        await sp.say("Sorry, I couldn't answer just now.", last=True)
                    break
            if not got and final_text:                              # a reply that came whole
                body = tag.feed(final_text)
                if tag.mood:
                    self._mood(tag.mood)
                for piece in chunker.feed(body):
                    await sp.say(piece)
            rest = chunker.flush()
            if rest:
                await sp.say(rest, last=True)
            await sp.close()
        except asyncio.CancelledError:
            await sp.abort()
            raise
        except Exception as e:
            self.log("warn", f"voice: reply failed ({type(e).__name__}: {e})")
            await sp.abort()
        finally:
            self.thinking = False

    def _mood(self, mood: str):
        global KNOWN_MOODS
        if KNOWN_MOODS is None:
            from .body import MOODS
            KNOWN_MOODS = set(MOODS)
        if mood in KNOWN_MOODS and not self.body.estopped:
            self.body.express(mood)

    # ---- speaking with a fallback ---------------------------------------------------------------------------------
    def local_speak(self, text: str):
        """Local Supertonic (the terminal Cyph's voice) for when Cartesia is down or has no key. Blocking."""
        if self._local_tts is None:
            from .local import LocalConfig, Tts
            c = LocalConfig()
            self._local_tts = Tts(c.voice, c.lang, c.tts_steps)
        self._play(self._local_tts.speak(text).tobytes())

    # ---- the background tick ----------------------------------------------------------------------------------------
    async def _tick(self):
        was_speaking = False
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            if self._ptt_was and not self._ptt_active():
                self._mic_sync()
            self._ptt_was = self._ptt_active()
            playing = self.speaker.playing
            self.speaking = playing or (self._reply is not None and self._reply.sent and not self._reply_done())
            if self.speaking:
                if not was_speaking:
                    self.body.set_speaking(True, level=0.0, quiet=True)
                self.body.feed_speech_level(float(getattr(self.speaker, "level", 0.0)))
            elif was_speaking:
                self.body.set_speaking(False, quiet=True)
            was_speaking = self.speaking
            if self._mic_on and not self.hearing and not self._ptt_active():
                self.level *= 0.8
            mins = self.cfg["sleep_after_min"]
            if (mins and self.cfg["mode"] == "always" and self.cfg["on"] and not self.asleep and not self.speaking
                    and not self.thinking and not self.hearing and now - self._active_at > mins * 60
                    and (self._wake_detector() is not None or (self.whisper_path and Path(self.whisper_path).exists()))):
                self.sleep()

    def _reply_done(self) -> bool:
        r = self._reply
        return r is None or r._reader is None or r._reader.done()


class _WhisperAsStt:
    """Local Whisper with the same transcribe/streaming surface as CartesiaStt (no streaming)."""
    streaming = False

    def __init__(self, get):
        self._get = get

    def transcribe(self, pcm):
        return self._get().transcribe(pcm)

    def resolve(self, fut, pcm):
        return self._get().transcribe(pcm)


class _Speech:
    """Speaks sentences for one turn: Cartesia when it works, else the local voice; keeps the engine's view of it current."""

    def __init__(self, eng: VoiceEngine):
        self.eng = eng
        self.reply = eng._new_reply()
        eng._reply = self.reply
        self.failed = self.reply is None
        self.mood = None
        self.local_q: list[str] = []
        self._prewarm = asyncio.create_task(self._open()) if self.reply is not None else None

    async def _open(self):
        try:
            await self.reply.open()                                  # connect while the brain is still thinking
        except Exception as e:
            self._fail(e)

    def _fail(self, e):
        if not self.failed:
            self.failed = True
            self.eng.tts_name = "local (cartesia failed)"
            self.eng.warn_once("tts", f"voice: Cartesia text-to-speech failed ({e}); using the local voice until it recovers")

    async def say(self, text: str, last=False):
        t = speakable(text)
        if not t:
            return
        if not self.failed:
            try:
                if self._prewarm is not None:
                    await self._prewarm
                    self._prewarm = None
                await self.reply.say(t)
                return
            except Exception as e:
                self._fail(e)
        self.local_q.append(t)

    async def close(self):
        if not self.failed and self.reply is not None:
            try:
                await self.reply.finish()
            except Exception as e:
                self._fail(e)
        loop = asyncio.get_running_loop()
        for t in self.local_q:
            try:
                await loop.run_in_executor(None, self.eng.local_speak, t)
            except Exception as e:
                self.eng.warn_once("local-tts", f"voice: the local voice failed too ({type(e).__name__}: {e})")
                break
        if self.eng._reply is self.reply:
            await self._wait_spoken()

    async def _wait_spoken(self):
        # the audio is still in the speaker's buffer: the turn is over when it has been played
        for _ in range(900):
            if not self.eng.speaker.playing:
                return
            await asyncio.sleep(0.1)

    async def abort(self):
        if self._prewarm is not None:
            self._prewarm.cancel()
        if self.reply is not None:
            await self.reply.cancel()
        self.eng.speaker.clear()
