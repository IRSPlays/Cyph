"""Gemini Live voice session for Cyph.

One VoiceSession owns the Live WebSocket, the mic and the speaker. It runs
in its own thread + event loop so the terminal UI and the 50 Hz body loop
are never blocked.

- mic -> Gemini as 16 kHz PCM; Gemini audio -> speaker at 24 kHz
- Gemini calls body tools (NON_BLOCKING) -> tools.dispatch -> Body
- local reflexes: speaker loudness -> wrist bob/grabber flap, mic loudness
  -> listening tilt, so reactions need no model round trip
- context-window compression + session resumption keep it alive past the
  15-minute audio limit; a GoAway message triggers a seamless reconnect
- the API key is read from the GEMINI_API_KEY environment variable (or an
  untracked cyph/.env) and is never written to disk or logged by this code
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from google import genai
from google.genai import types

from . import audio
from .body import Body
from .tools import CORE_TOOLS, SYSTEM_INSTRUCTION, declarations, dispatch

MODELS = {
    "live": "gemini-3.8-live",
    "thinking": "gemini-3.8-live-extended-thinking",
}
DEFAULT_VOICE = "Alnilam"    # "Firm, lower middle pitch"
LISTEN_THRESHOLD = 0.06     # mic level that counts as the user talking
ECHO_TAIL_S = 0.35           # keep the mic muted a moment after playback
FATAL_HINTS = ("API key", "API_KEY", "PERMISSION_DENIED", "UNAUTHENTICATED",
               "401", "403")


KEY_SOURCE = ""      # where the last key came from (never the key itself)


def load_api_key() -> str | None:
    """GEMINI_API_KEY, then cyph/.env, then GOOGLE_API_KEY last: a stale
    GOOGLE_API_KEY left in the Windows environment must not shadow the key the
    user put in .env (it produced 'API key not valid', close code 1007)."""
    global KEY_SOURCE
    key = os.environ.get("GEMINI_API_KEY")
    if key and key.strip():
        KEY_SOURCE = "GEMINI_API_KEY environment variable"
        return key.strip()
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val:
                    KEY_SOURCE = "cyph/.env"
                    return val
    key = os.environ.get("GOOGLE_API_KEY")
    if key and key.strip():
        KEY_SOURCE = "GOOGLE_API_KEY environment variable"
        return key.strip()
    return None


SCHEDULING = {
    "silent": types.FunctionResponseScheduling.SILENT,
    "when_idle": types.FunctionResponseScheduling.WHEN_IDLE,
    "interrupt": types.FunctionResponseScheduling.INTERRUPT,
    "none": None,
}


def _shorten(o):
    if isinstance(o, dict):
        return {k: _shorten(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_shorten(v) for v in o]
    if isinstance(o, (bytes, bytearray)):
        return f"<{len(o)} bytes>"
    if isinstance(o, str) and len(o) > 60:
        return o[:40] + f"...<{len(o)} chars>"
    return o


def raw_dump(msg, limit=260) -> str:
    """Compact dump of every populated field, so nothing hides from --debug."""
    try:
        text = str(_shorten(msg.model_dump(exclude_none=True)))
    except Exception as e:                        # never let logging break the loop
        text = f"<dump failed: {e}>"
    return text if len(text) <= limit else text[:limit] + "..."


def describe(msg) -> str:
    """One-line summary of a server message, for --debug."""
    bits = []
    for name in sorted(msg.model_fields_set):
        v = getattr(msg, name, None)
        if name == "server_content" and v is not None:
            sc = []
            if v.model_turn:
                for p in v.model_turn.parts or []:
                    if p.inline_data and p.inline_data.data:
                        sc.append(f"audio {len(p.inline_data.data)}B")
                    elif p.text:
                        sc.append(f"text {p.text[:40]!r}")
                    else:
                        sc.append("part")
            for flag in ("turn_complete", "generation_complete", "interrupted"):
                if getattr(v, flag, None):
                    sc.append(flag)
            for t in ("input_transcription", "output_transcription"):
                tr = getattr(v, t, None)
                if tr and tr.text:
                    sc.append(f"{t}={tr.text[:30]!r}")
            bits.append("server_content[" + ", ".join(sc) + "]")
        elif name == "tool_call" and v is not None:
            bits.append("tool_call[" + ", ".join(f.name for f in (v.function_calls or [])) + "]")
        elif name == "session_resumption_update" and v is not None:
            bits.append(f"resumption(resumable={v.resumable})")
        elif name == "go_away":
            bits.append(f"go_away({getattr(v, 'time_left', None)})")
        else:
            bits.append(name)
    status = getattr(msg, "interaction_status", None)
    if status:
        bits.append(f"interaction_status={status}")
    return " ".join(bits) or "(empty message)"


class VoiceSession:
    def __init__(self, body: Body, api_key: str | None = None,
                 model: str = MODELS["live"], voice: str = DEFAULT_VOICE,
                 mic=None, speaker=None, barge_in: bool = False,
                 greet: bool = True, client=None, disable=(), debug: bool = False,
                 scheduling: str = "none", camera=None, video_fps: float = 1.0,
                 media_resolution: str | None = None, video_fallback: bool = True):
        self.body = body
        self.media_resolution = media_resolution    # None | low | medium | high
        self.video_fallback = video_fallback
        self.camera = camera           # vision.Camera or None
        self.video_interval = 1.0 / max(0.1, min(1.0, video_fps))   # docs: max 1 frame/s
        self.sent_video = 0
        self.video_on = camera is not None     # dropped automatically if the server rejects frames
        self.model = MODELS.get(model, model)
        self.voice = voice
        self.mic = mic if mic is not None else audio.Mic()
        self.speaker = speaker if speaker is not None else audio.Speaker()
        self.barge_in = barge_in
        self.greet = greet
        self.disable = set(disable)   # tools, transcription, compression, vad
        self.debug = debug
        self.scheduling = scheduling   # none (default) | silent | when_idle | interrupt; SILENT made the model mute
        self.client = client or genai.Client(
            api_key=api_key, http_options={"api_version": "v1beta"})

        self.state = "off"          # off | connecting | live | error
        self.handle = None          # session-resumption handle
        self.tool_calls = 0
        self._in = ""
        self._out = ""
        self._reconnect = False
        self._speaking = False
        self._listening = False
        self._heard_at = 0.0
        self._floor = None
        self._convo_at = 0.0
        self._guard_until = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._loop = None
        self._task = None
        self._mic_q = None
        self._session = None
        self._ready = None          # set once the server sends setup_complete
        self.msg_count = 0          # server messages seen (diagnostics)
        self.sent_text = 0
        self.last_msg_at = 0.0
        self.sent_audio = 0

    # ---- control (thread-safe) -------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = "connecting"
        self._log("info", f"voice: using the key from the {KEY_SOURCE or 'caller'}")
        self._thread = threading.Thread(target=self._thread_main,
                                        name="cyph-voice", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._thread:
            self._thread.join(timeout=3.0)
        self.state = "off"

    def toggle(self):
        if self.state == "off" or self.state == "error":
            self.start()
        else:
            self.stop()

    def send_text(self, text: str):
        """Type to Cyph instead of speaking (also used by the self-test)."""
        if not (self._loop and self.state == "live"):
            return
        fut = asyncio.run_coroutine_threadsafe(self._send_text(text), self._loop)

        def done(f):
            if f.cancelled():
                return
            exc = f.exception()
            if exc:
                self._log("error", f"send_text failed: {type(exc).__name__}: {exc}")
        fut.add_done_callback(done)

    # ---- internals ---------------------------------------------------------

    def _log(self, level, text):
        self.body.events.put((level, text))

    def _dbg(self, text):
        if self.debug:
            self.body.events.put(("debug", text))

    def _config(self) -> types.LiveConnectConfig:
        off = self.disable
        kw = dict(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_INSTRUCTION,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice))),
        )
        if "resumption" not in off:
            kw["session_resumption"] = types.SessionResumptionConfig(handle=self.handle)
        if self.media_resolution:
            kw["media_resolution"] = getattr(
                types.MediaResolution, f"MEDIA_RESOLUTION_{self.media_resolution.upper()}")
        if "tools" not in off:
            only = CORE_TOOLS if "newtools" in off else None
            kw["tools"] = [types.Tool(function_declarations=declarations(only))]
        if "transcription" not in off:
            kw["input_audio_transcription"] = types.AudioTranscriptionConfig()
            kw["output_audio_transcription"] = types.AudioTranscriptionConfig()
        if "compression" not in off:
            kw["context_window_compression"] = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow())
        if "vad" not in off:
            kw["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    silence_duration_ms=700, prefix_padding_ms=100))
        if "extended-thinking" in self.model:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level="low")
        return types.LiveConnectConfig(**kw)

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._main())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._loop.close()
            self._loop = None
            if self.state != "error":
                self.state = "off"

    async def _main(self):
        failures, backoff = 0, 1.0
        try:
            self.speaker.start()
        except Exception as e:
            self._log("error", f"voice: cannot open speaker: {e}")
            self.state = "error"
            self.body.sfx_play("error")
            return
        try:
            while not self._stop.is_set():
                try:
                    await self._connect_once()
                    failures, backoff = 0, 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    failures += 1
                    msg = f"{type(e).__name__}: {e}"
                    self._log("error", f"voice: {msg[:300]}")
                    if any(h in msg for h in ("not found", "expired", "1007")):
                        self.handle = None       # start fresh next time
                    if "API key" in msg or "API_KEY" in msg:
                        self._log("error", f"voice: the key from the {KEY_SOURCE or 'environment'} "
                                           "was rejected. Set GEMINI_API_KEY in this terminal (or "
                                           "put it in cyph/.env) and remove an old GOOGLE_API_KEY.")
                    elif ("1007" in msg and self.video_on and self.sent_video > 0
                          and self.video_fallback):
                        # the key is fine and frames were flowing: stop the camera stream
                        self.video_on = False
                        self._log("warn", "voice: server rejected the camera frames (1007); "
                                          "continuing without video. Run with --debug for details.")
                        self.state = "connecting"
                        continue
                    if any(h in msg for h in FATAL_HINTS) or failures >= 5:
                        self._log("error", "voice: giving up - check the key, "
                                           "model access and network, then press v")
                        self.state = "error"
                        self.body.sfx_play("error")
                        return
                    self.state = "connecting"
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 15.0)
        finally:
            self.body.set_speaking(False, quiet=True)
            self.body.set_listening(False, quiet=True)
            self.mic.stop()
            self.speaker.stop()

    async def _connect_once(self):
        self.state = "connecting"
        self._reconnect = False
        self._mic_q = asyncio.Queue(maxsize=50)
        self._ready = asyncio.Event()
        first = self.handle is None
        async with self.client.aio.live.connect(
                model=self.model, config=self._config()) as session:
            self._session = session
            self.msg_count = 0
            self._ready.set()    # connect() already read setup_complete for us
            self.state = "live"
            self._log("info", f"voice live: {self.model}")
            self._dbg(f"connected: session_id={getattr(session, 'session_id', None)} "
                      f"setup_complete={getattr(session, 'setup_complete', None) is not None} "
                      f"resumed={not first}")
            self.body.converse()         # model drives the body now, not idle moods
            if first:
                self.body.wake()
            self.mic.start(self._loop, self._mic_q)
            self._log("info", f"mic: {self.mic.label} | speaker: {self.speaker.label}")
            tasks = [asyncio.create_task(self._send_mic(session)),
                     asyncio.create_task(self._monitor()),
                     asyncio.create_task(self._receive(session))]
            if self.camera is not None and self.video_on:
                tasks.append(asyncio.create_task(self._send_video(session)))
            if first and self.greet:
                tasks.append(asyncio.create_task(self._send_text("Hey Cyph!", session)))
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    d.result()           # re-raise a failure so we reconnect
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.mic.stop()

    async def _send_text(self, text, session=None):
        session = session or self._session
        if session is None:
            return
        self._dbg(f"-> realtime_input.text ({len(text)} chars): {text[:60]!r}")
        await session.send_realtime_input(text=text)
        self.sent_text += 1

    async def _send_mic(self, session):
        muted = False
        while True:
            chunk = await self._mic_q.get()
            now = time.monotonic()
            if self.body.sfx_busy():
                continue                 # a sound effect is playing: do not let Gemini hear it
            if not self.barge_in and (self.speaker.playing or now < self._guard_until):
                if not muted:
                    self._dbg("mic muted while Cyph speaks (echo guard; --barge-in disables)")
                    muted = True
                continue                 # speakers would feed Cyph its own voice
            if muted:
                self._dbg("mic live again")
                muted = False
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
            self.sent_audio += 1
            if self.sent_audio == 1 or self.sent_audio % 250 == 0:
                self._dbg(f"-> realtime_input.audio chunks sent: {self.sent_audio} "
                          f"(mic level {self.mic.level:.2f})")

    async def _send_video(self, session):
        """Camera frames to Gemini as JPEG realtime input (about 1 per second)."""
        while True:
            await asyncio.sleep(self.video_interval)
            data = self.camera.jpeg() if self.camera.state == "live" else None
            if data is None:
                continue
            await session.send_realtime_input(
                video=types.Blob(data=data, mime_type="image/jpeg"))
            self.sent_video += 1
            if self.sent_video == 1 or self.sent_video % 60 == 0:
                self._dbg(f"-> realtime_input.video frames sent: {self.sent_video} "
                          f"({len(data) // 1024} KB each)")

    async def _monitor(self):
        """Local reflexes: mirror speaker/mic loudness onto the body."""
        t_start, warned = time.monotonic(), False
        while True:
            now = time.monotonic()
            if (not warned and now - t_start > 10 and self.mic.label != "none"
                    and self.mic.peak < 0.01):
                warned = True
                self._log("warn", f"the mic ({self.mic.label}) has been silent for 10 s: wrong "
                                  "device, muted, or the Iriun phone app is not connected. "
                                  "Try: python -m cyph mic-test")
            playing = self.speaker.playing
            if playing:
                self._guard_until = now + ECHO_TAIL_S
            if playing != self._speaking:
                self._speaking = playing
                self.body.set_speaking(playing, level=0.0 if playing else None, quiet=True)
            if playing:
                self.body.feed_speech_level(self.speaker.level)
            lvl = self.mic.level
            # adaptive noise floor: drops at once, rises slowly. The Iriun mic idles at
            # ~0.06, above a fixed threshold, which would keep "listening" on forever.
            self._floor = min(lvl, self._floor + 0.0005) if self._floor is not None else lvl
            if lvl > max(LISTEN_THRESHOLD, self._floor * 2.0 + 0.03) and not playing:
                self._heard_at = now
            listening = (now - self._heard_at) < 0.8 and not playing
            if (playing or listening) and now - self._convo_at > 1.0:
                self._convo_at = now
                self.body.converse()     # keep idle moods off while we talk
            if listening != self._listening:
                self._listening = listening
                self.body.set_listening(listening, quiet=True)
            await asyncio.sleep(0.05)

    async def _receive(self, session):
        while not self._stop.is_set():
            async for msg in session.receive():     # ends at each turn_complete
                await self._handle(session, msg)
                if self._reconnect:
                    return
            self._dbg("receive() iterator ended (normal after turn_complete); listening again")

    def _flush_transcripts(self):
        if self._in.strip():
            self._log("you", self._in.strip())
        if self._out.strip():
            self._log("cyph", self._out.strip())
        self._in = self._out = ""

    async def _handle(self, session, msg: types.LiveServerMessage):
        self.msg_count += 1
        self.last_msg_at = time.monotonic()
        if self.debug:
            self._dbg("<- " + describe(msg) + "   RAW " + raw_dump(msg))
        sc = msg.server_content
        if sc:
            if sc.interrupted:
                self.speaker.clear()
                self._flush_transcripts()
            if sc.model_turn:
                for part in sc.model_turn.parts or []:
                    if part.inline_data and part.inline_data.data:
                        self.speaker.write(part.inline_data.data)
            if sc.input_transcription and sc.input_transcription.text:
                self._in += sc.input_transcription.text
            if sc.output_transcription and sc.output_transcription.text:
                self._out += sc.output_transcription.text
            if sc.turn_complete:
                self._flush_transcripts()

        if msg.tool_call and msg.tool_call.function_calls:
            responses = []
            for fc in msg.tool_call.function_calls:
                args = dict(fc.args or {})
                result = dispatch(self.body, fc.name, args)
                self.body.converse()
                self.tool_calls += 1
                self.body.sfx_play("tool")
                self._log("tool", f"{fc.name}({', '.join(f'{k}={v}' for k, v in args.items())})"
                                  + (f" -> {result['error']}" if "error" in result else ""))
                extra = {}
                sched = SCHEDULING.get(self.scheduling)
                if sched is not None and "extended-thinking" not in self.model:
                    extra["scheduling"] = sched      # unsupported on extended-thinking
                responses.append(types.FunctionResponse(
                    id=fc.id, name=fc.name, response=result, **extra))
            await session.send_tool_response(function_responses=responses)

        if msg.tool_call_cancellation:
            self._log("warn", f"tool calls cancelled: {msg.tool_call_cancellation.ids}")

        u = msg.session_resumption_update
        if u and u.resumable and u.new_handle:
            self.handle = u.new_handle

        if msg.go_away is not None:
            self._log("warn", f"server going away ({msg.go_away.time_left}); reconnecting")
            self._reconnect = True
