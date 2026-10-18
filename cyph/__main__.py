"""python -m cyph

Everything is on by default: arm on COM15, Gemini Live voice (auto-connect),
mic 1, camera 1. Needs the GEMINI_API_KEY environment variable (or cyph/.env).

    python -m cyph                  # the full Cyph
    python -m cyph --sim            # no arm, simulator only
    python -m cyph --no-voice --no-camera --sim
    python -m cyph devices          # list audio devices
    python -m cyph cameras          # list webcams
    python -m cyph cam-test         # snapshot + person-detector check
    python -m cyph voice-test       # headless Gemini connection check
"""
import argparse
import sys

from .body import Body, SerialBackend, SimBackend
from .local import MODEL_DIR
from .voice import DEFAULT_VOICE

DEFAULT_PORT = "COM15"
DEFAULT_MIC = "Iriun"            # matched by NAME: device numbers change when USB/Bluetooth changes
DEFAULT_SPEAKER = "CMF Buds 2"   # CMF Buds 2 Plus earbuds (output)
DEFAULT_CAMERA = 1       # the arm camera (0 = Iriun, 3 = OBS virtual cam)

NO_KEY = (
    "No API key found. Set it for this terminal, then run again:\n"
    "  PowerShell:  $env:GEMINI_API_KEY = \"<your key>\"\n"
    "or put GEMINI_API_KEY=<your key> in cyph/.env (never committed)."
)


def cameras():
    from .vision import list_cameras
    found = list_cameras()
    if not found:
        print("no camera returned a frame (is it plugged in and not used by another app?)")
    for i, w, h in found:
        print(f"  camera {i}: {w}x{h}")
    print("use:  python -m cyph --camera <index> ...   (python -m cyph cam-test --camera <index> "
          "saves a snapshot)")


def cam_test(a):
    import time
    from .vision import Camera
    body = Body(SimBackend())
    cam = Camera(a.camera or 0, body, mirror=a.mirror)
    cam.start()
    t0, seen = time.time(), 0
    while time.time() - t0 < 8:
        time.sleep(0.25)
        if cam.state == "error":
            break
        if cam.person:
            seen += 1
    for line in _drain(body):
        print(line)
    if cam.state == "live":
        ok = cam.snapshot("cyph_snapshot.jpg")
        print(f"state: live {'STEREO' if cam.stereo else 'single lens'}, eye {cam.size[0]}x{cam.size[1]}, "
              f"{cam.fps:.0f} fps, detector: {cam.detector_kind}")
        print(f"person found in {seen} of ~32 checks"
              + (f"; last position x={cam.person[0]:+.2f} y={cam.person[1]:+.2f}" if cam.person else ""))
        if cam.person and cam.person[2]:
            print(f"person distance: {cam.person[2] * 100:.0f} cm")
        m = cam.measure(0.0, 0.0)
        print(f"centre of the image: {m}")
        print("saved cyph_snapshot.jpg" if ok else "snapshot failed")
    cam.stop()


def build_local(body, a, camera=None):
    from . import audio
    from .bridge import SharedChat
    from .local import LocalConfig, LocalSession
    cfg = LocalConfig(llm=a.llm, voice=a.local_voice, port=a.llm_port or 0, tts_steps=a.tts_steps,
                      stt="cartesia" if a.stt == "cartesia" else "whisper",
                      whisper=str(MODEL_DIR / f"whisper-{'base' if a.stt == 'cartesia' else a.stt}.en"))
    memory = curator = None
    if not a.no_memory:
        from .memory import Curator, MemoryStore
        memory = MemoryStore()
        curator = Curator(memory, model=a.memory_model)
    sess = LocalSession(body, cfg, mic=audio.Mic(a.mic), speaker=audio.Speaker(a.speaker),
                        barge_in=not a.no_barge_in, camera=camera, debug=a.debug,
                        memory=memory, curator=curator,
                        bridge=not a.no_bridge, shared=SharedChat() if not a.no_bridge else None)
    if curator is not None:
        curator.log = sess._log
    sess.ambient = not a.no_ambient
    return sess


def local_test(a):
    """Check the local brain end to end without a mic: Supertonic speaks a sentence, Silero and Whisper
    hear it back, Qwen answers, and the timings are printed."""
    import time
    import numpy as np
    from .local import LlamaServer, LlmClient, LocalConfig, Stt, Tts, Vad, SAMPLE_16K, local_system_prompt
    import asyncio
    cfg = LocalConfig(llm=a.llm, voice=a.local_voice, port=a.llm_port or 0, tts_steps=a.tts_steps,
                      stt="cartesia" if a.stt == "cartesia" else "whisper",
                      whisper=str(MODEL_DIR / f"whisper-{'base' if a.stt == 'cartesia' else a.stt}.en"))
    t = time.time()
    srv = LlamaServer(cfg, lambda l, x: print(f"[{l}] {x}"))
    srv.start()
    print(f"llama-server ready ({time.time() - t:.1f} s)")
    tts, stt, vad = Tts(cfg.voice, cfg.lang, cfg.tts_steps), Stt(cfg.whisper), Vad()
    phrase = "Hey Cyph, what is the capital of France?"
    t = time.time()
    pcm24 = tts.speak(phrase)
    print(f"TTS: {len(pcm24) / 24000:.1f} s of audio in {time.time() - t:.2f} s")
    x = np.interp(np.linspace(0, len(pcm24) - 1, int(len(pcm24) * SAMPLE_16K / 24000)),
                  np.arange(len(pcm24)), pcm24.astype(np.float32)).astype(np.int16)
    probs = [vad(x[i:i + 512]) for i in range(0, len(x) - 512, 512)]
    print(f"VAD: speech in {sum(p > 0.5 for p in probs)} of {len(probs)} frames")
    t = time.time()
    heard = stt.transcribe(x)
    print(f"STT: {heard!r} in {time.time() - t:.2f} s")
    llm = LlmClient(cfg, srv.url)

    async def ask():
        t0, first, text = time.time(), None, ""
        async for k, v in llm.stream([{"role": "system", "content": local_system_prompt()},
                                      {"role": "user", "content": heard or phrase}], None):
            if k == "text":
                first = first or time.time() - t0
                text += v
        print(f"LLM: first token {first:.2f} s, total {time.time() - t0:.2f} s -> {text!r}")
    asyncio.run(ask())
    srv.stop()


def build_voice(body, a, camera=None):
    from . import audio
    from .voice import MODELS, VoiceSession, load_api_key
    key = load_api_key()
    if not key:
        raise SystemExit(NO_KEY)
    return VoiceSession(
        body, key, model=MODELS[a.voice_model], voice=a.voice_name,
        mic=audio.Mic(a.mic), speaker=audio.Speaker(a.speaker),
        barge_in=not a.no_barge_in, debug=a.debug, scheduling=a.scheduling,
        camera=camera, video_fps=a.video_fps)


def _drain(body):
    out = []
    while not body.events.empty():
        lvl, txt = body.events.get_nowait()
        out.append(f"[{lvl}] {txt}")
    return out


PROFILES = [
    # (label, disabled features, tool-response scheduling)
    ("full config, tool replies with no scheduling", (), "none"),
    ("full config, tool replies SILENT", (), "silent"),
    ("full config, tool replies WHEN_IDLE", (), "when_idle"),
    ("no tools at all (does it talk?)", ("tools",), "none"),
]


def _drain_all(body):
    out = []
    while not body.events.empty():
        out.append(body.events.get_nowait())
    return out


def _try_profile(a, key, label, disable, sched, prompt):
    import time
    from . import audio
    from .voice import MODELS, VoiceSession
    body = Body(SimBackend())
    body.start()
    spk = audio.CountingSpeaker()
    v = VoiceSession(body, key, model=MODELS[a.voice_model], voice=a.voice_name,
                     mic=audio.NullMic(), speaker=spk, greet=False,
                     disable=disable, debug=True, scheduling=sched)
    errors = []

    def show():
        for lv, t in _drain_all(body):
            if lv in ("mood",) or t.startswith(("listening", "speaking")):
                continue
            print(f"   [{lv}] {t}", flush=True)
            if lv == "error":
                errors.append(t)

    v.start()
    t0 = time.time()
    while v.state not in ("live", "error") and time.time() - t0 < 20:
        time.sleep(0.2)
        show()
    show()
    if v.state == "live":
        v.send_text(prompt)
    t0 = time.time()
    while time.time() - t0 < 25 and v.state != "error":
        time.sleep(0.2)
        show()
        if spk.bytes > 0 and (v.tool_calls > 0 or "tools" in disable):
            break
        # the reply is over once the server has gone quiet for a while
        if v.msg_count >= 3 and time.monotonic() - v.last_msg_at > 6:
            break
    time.sleep(1.5)
    show()
    res = dict(label=label, state=v.state, messages=v.msg_count, audio=spk.bytes,
               tools=v.tool_calls, errors=errors[:2])
    v.stop()
    body.close()
    return res


def voice_test(a):
    """Headless check of the Gemini link: no mic, no speakers, no arm.
    Tries several configurations and prints a summary table."""
    from .voice import load_api_key
    key = load_api_key()
    if not key:
        raise SystemExit(NO_KEY)
    prompt = ("Say hello to Cypher in one short sentence, and while you do, "
              "act happy with your body and then nod.")
    print("(headless test: simulated body, no arm, no sound)")
    results = []
    for label, disable, sched in PROFILES:
        print(f"\n-> {label}", flush=True)
        r = _try_profile(a, key, label, disable, sched, prompt)
        results.append((r, sched))
        print(f"   => state={r['state']} messages={r['messages']} "
              f"audio={r['audio']} B tool calls={r['tools']}", flush=True)
        if r["audio"] > 0 and (r["tools"] > 0 or "tools" in disable):
            print(f"\nRESULT: PASS ({label})")
            sys.exit(0)
    print("\nSUMMARY")
    for r, sched in results:
        print(f"  {r['label']:<48} audio={r['audio']:>7} B  tools={r['tools']}")
    print("\nRESULT: no configuration produced both speech and tool calls. "
          "Paste everything above.")
    sys.exit(1)


VIDEO_PROBES = [
    # (label, disabled features, media_resolution, stream video?)
    ("text only: no tools, no video", ("tools",), None, False),
    ("all tools, no video", (), None, False),
    ("no tools + video", ("tools",), None, True),
    ("original 4 tools + video", ("newtools",), None, True),
    ("all tools + video  (what the app does)", (), None, True),
    ("all tools + video, no context compression", ("compression",), None, True),
    ("all tools + video, no session resumption", ("resumption",), None, True),
    ("all tools + video, no compression and no resumption", ("compression", "resumption"), None, True),
    ("all tools + video, no VAD settings", ("vad",), None, True),
    ("all tools + video, media_resolution LOW", (), "low", True),
    ("all tools + video, no transcription", ("transcription",), None, True),
]


class _SyntheticCam:
    """A 640x360 JPEG with some structure, so video-test needs no camera."""
    state = "live"

    def __init__(self):
        import cv2
        import numpy as np
        img = np.zeros((360, 640, 3), np.uint8)
        for x in range(640):
            img[:, x] = (x * 255 // 640, 120, 255 - x * 255 // 640)
        cv2.circle(img, (320, 180), 70, (240, 240, 240), -1)
        cv2.putText(img, "CYPH TEST", (210, 320), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
        self._data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()

    def jpeg(self):
        return self._data


def _video_probe(a, key, label, disable, media_res, video, cam):
    import time
    from . import audio
    from .voice import MODELS, VoiceSession
    body = Body(SimBackend())
    body.start()
    spk = audio.CountingSpeaker()
    v = VoiceSession(body, key, model=MODELS[a.voice_model], voice=a.voice_name,
                     mic=audio.NullMic(), speaker=spk, greet=False, disable=disable,
                     media_resolution=media_res, camera=cam if video else None,
                     video_fps=1.0, video_fallback=False)
    err = ""
    v.start()
    t0, sent = time.time(), False
    while time.time() - t0 < 25 and not err:
        time.sleep(0.2)
        for lv, t in _drain_all(body):
            if lv == "error" and not err:          # keep the FIRST error, not "giving up"
                err = t.replace(key, "<key>")
        if v.state == "live" and not sent and (not video or v.sent_video >= 1):
            v.send_text("Say hello to Cypher in one short sentence.")
            sent = True
        if spk.bytes > 0 and (not video or v.sent_video >= 3):
            break
    time.sleep(0.5)
    res = dict(label=label, ok=not err and spk.bytes > 0, audio=spk.bytes,
               frames=v.sent_video, state=v.state, error=err[:230])
    v.stop()
    body.close()
    return res


def video_test(a):
    """Real Gemini connection tried with several configurations, to see which
    one the server rejects (close code 1007) when camera frames are streamed.
    Needs the key. Headless: simulated body, no mic, no speaker, no arm."""
    from .voice import KEY_SOURCE, load_api_key
    key = load_api_key()
    if not key:
        raise SystemExit(NO_KEY)
    from . import voice as voice_mod
    print(f"(using the key from the {voice_mod.KEY_SOURCE}; nothing is printed that contains it)")
    preflight(key, voice_mod.KEY_SOURCE)
    probes = list(VIDEO_PROBES)
    real = None
    if not a.no_camera:
        try:
            from .vision import Camera
            real = Camera(a.camera, None, track=False)
            real.start()
            import time
            for _ in range(40):
                time.sleep(0.25)
                if real.state in ("live", "error"):
                    break
            if real.state == "live":
                probes.append(("all tools + REAL camera frames", (), None, True))
            else:
                real = None
        except Exception:
            real = None
    synth = _SyntheticCam()
    results = []
    for label, disable, media_res, video in probes:
        print(f"\n-> {label}", flush=True)
        cam = real if (real is not None and label.endswith("REAL camera frames")) else synth
        r = _video_probe(a, key, label, disable, media_res, video, cam)
        results.append(r)
        print(f"   {'OK  ' if r['ok'] else 'FAIL'} state={r['state']} frames sent={r['frames']} "
              f"audio={r['audio']} B" + (f"\n   {r['error']}" if r["error"] else ""), flush=True)
    if real is not None:
        real.stop()
    print("\nSUMMARY")
    for r in results:
        print(f"  {'OK  ' if r['ok'] else 'FAIL'}  {r['label']}")
    bad = [r for r in results if not r["ok"]]
    if not bad:
        print("\nEverything works. If the app still fails, run it with --debug and paste the log.")
    else:
        print("\nPaste this whole output. The first FAIL that follows an OK row names the cause.")


def preflight(key, source):
    """Ask the API for one model name before any Live test: a rejected key is
    reported once, plainly, instead of as 12 identical 1007 failures."""
    from google import genai
    try:
        client = genai.Client(api_key=key)      # keep a reference or it is closed under us
        next(iter(client.models.list(config={"page_size": 1})))
        return
    except Exception as e:
        why = str(e).replace(key, "<key>")[:140]
    hint = " (that is only %d characters: it looks like a placeholder, not a real key)" % len(key) \
        if len(key) < 30 else ""
    raise SystemExit(
        f"STOP: Google rejects the key from the {source}{hint}.\n  {why}\n\n"
        "This is not a Live API or video problem; nothing else can work until the key is valid.\n"
        "Fix (pick one), then run again:\n"
        "  1. Put your real key in cyph/.env as one line:  GEMINI_API_KEY=<your key>\n"
        "     (cyph/.env is used before GOOGLE_API_KEY and is never committed)\n"
        "  2. or in this terminal:  $env:GEMINI_API_KEY = \"<your key>\"\n"
        "And delete the old placeholder:\n"
        "  [Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', $null, 'User')\n"
        "  (then open a new terminal)")


def key_check():
    """Diagnose a rejected key without ever printing it."""
    from google import genai
    from .voice import load_api_key
    key = load_api_key()
    if not key:
        raise SystemExit(NO_KEY)
    print(f"key: {key[:3]}... length {len(key)}")
    if key != key.strip() or any(c in key for c in (" ", chr(9), chr(13), chr(10), chr(34), chr(39))):
        print("  PROBLEM: the key contains spaces, quotes or line breaks - re-set it cleanly")
    for label, kw in (("Gemini Developer API", {}), ("Vertex AI express mode", {"vertexai": True})):
        try:
            c = genai.Client(api_key=key, **kw)
            next(iter(c.models.list(config={"page_size": 1})))
            print(f"  {label}: ACCEPTED")
        except Exception as e:
            print(f"  {label}: rejected - {type(e).__name__}: {str(e).replace(key, '<key>')[:160]}")


def sfx_test(a):
    """Audition every sound effect (or one with --sfx-name), or export them with --sfx-out DIR."""
    from . import sfx
    todo = [a.sfx_name] if a.sfx_name else sfx.names()
    bad = [n for n in todo if n not in sfx.RECIPES]
    if bad:
        raise SystemExit(f"unknown sound {bad[0]!r}. Sounds: {', '.join(sfx.names())}")
    if a.sfx_out:
        for p in sfx.export_wavs(a.sfx_out, todo):
            print("wrote", p)
        return
    import time
    player = sfx.SfxPlayer(a.speaker, max(0.0, min(1.0, a.sfx_volume)))
    player.start()
    print(f"sound effects on: {player.label}  (volume {player.volume:g})")
    if not player.enabled:
        raise SystemExit("could not open an output device. Run  python -m cyph devices")
    for n in todo:
        x = sfx.render(n)
        print(f"  {n:<9} {len(x) / sfx.SR:4.2f}s")
        player.play(n)
        time.sleep(len(x) / sfx.SR + 0.35)
    player.stop()


def main():
    ap = argparse.ArgumentParser(prog="cyph")
    ap.add_argument("command", nargs="?",
                    choices=["devices", "mic-test", "voice-test", "video-test", "key-check", "sfx-test",
                             "cameras", "cam-test", "local-test", "memory", "memory-import", "telegram"])
    ap.add_argument("--camera", type=int, default=DEFAULT_CAMERA,
                    help=f"webcam index (default {DEFAULT_CAMERA}; see `python -m cyph cameras`)")
    ap.add_argument("--no-camera", action="store_true", help="run without the camera")
    ap.add_argument("--no-video", action="store_true",
                    help="keep the camera for tracking but do not stream frames to Gemini")
    ap.add_argument("--mirror", action="store_true", help="flip the camera image left/right")
    ap.add_argument("--track-flip", action="store_true",
                    help="reverse the tracking direction (default already follows you)")
    ap.add_argument("--track-gain", type=float, default=None,
                    help="multiplier on the tracking angle (default 1.0)")
    ap.add_argument("--hfov", type=float, default=None,
                    help="camera horizontal field of view in degrees (default 100; "
                         "tune live with /hfov N)")
    ap.add_argument("--video-fps", type=float, default=1.0,
                    help="camera frames/s sent to Gemini (API maximum is 1)")
    ap.add_argument("--port", default=DEFAULT_PORT,
                    help=f"serial port of the arm (default {DEFAULT_PORT})")
    ap.add_argument("--sim", action="store_true", help="no arm: run against the simulator")
    ap.add_argument("--wild", type=float, default=1.5)
    ap.add_argument("--no-voice", action="store_true", help="do not use Gemini Live")
    ap.add_argument("--no-start-voice", action="store_true",
                    help="do not connect to Gemini on launch (press v)")
    ap.add_argument("--voice", action="store_true", help=argparse.SUPPRESS)        # old, now default
    ap.add_argument("--start-voice", action="store_true", help=argparse.SUPPRESS)  # old, now default
    ap.add_argument("--brain", choices=["gemini", "local"], default="gemini",
                    help="gemini = Gemini Live (cloud); local = Qwen3-VL + Whisper + Supertonic on this PC")
    ap.add_argument("--local-voice", default="M1", help="Supertonic voice for --brain local (M1..M5, F1..F5)")
    ap.add_argument("--llm", choices=["qwen", "qwen35"], default="qwen35",
                    help="local model: qwen35 = Qwen3.5-4B (default), qwen = Qwen3-VL-4B")
    ap.add_argument("--no-memory", action="store_true",
                    help="--brain local: do not load or save long-term memory about you")
    ap.add_argument("--memory-model", default="gemini-3.5-flash-lite",
                    help="Gemini model that curates memory (reads the conversation after it ends)")
    ap.add_argument("--path", default="", help="memory-import: the Gemini export (file or folder)")
    ap.add_argument("--apply", action="store_true", help="memory-import: write the result into live memory")
    ap.add_argument("--no-bridge", action="store_true",
                    help="--brain local: do not accept Telegram messages / share the chat with the phone bot")
    ap.add_argument("--no-ambient", action="store_true",
                    help="--brain local: do not react to the camera on its own (presence, pats, waving)")
    ap.add_argument("--stt", choices=["cartesia", "base", "small"], default="cartesia",
                    help="speech-to-text for --brain local: cartesia = Ink-2 streamed (needs CARTESIA_API_KEY, "
                         "falls back to local Whisper), base/small = local Whisper only")
    ap.add_argument("--tts-steps", type=int, default=5,
                    help="Supertonic quality steps for --brain local: 8 best, 3 fastest (default 5)")
    ap.add_argument("--llm-port", type=int, default=0, help="llama-server port for --brain local (default: per model)")
    ap.add_argument("--voice-model", choices=["live", "thinking"], default="live")
    ap.add_argument("--voice-name", default=DEFAULT_VOICE,
                    help="Gemini voice (default Alnilam: firm, lower middle pitch)")
    ap.add_argument("--mic", default=DEFAULT_MIC,
                    help=f"microphone: a name fragment or device number (default '{DEFAULT_MIC}')")
    ap.add_argument("--speaker", default=DEFAULT_SPEAKER,
                    help=f"speaker: a name fragment or device number (default '{DEFAULT_SPEAKER}')")
    ap.add_argument("--no-barge-in", action="store_true",
                    help="mute the mic while Cyph speaks (use with loudspeakers)")
    ap.add_argument("--no-sfx", action="store_true", help="no sound effects")
    ap.add_argument("--sfx-volume", type=float, default=0.35,
                    help="sound effect volume 0..1 (default 0.35)")
    ap.add_argument("--sfx-name", default="", help="sfx-test: play only this sound")
    ap.add_argument("--sfx-out", default="", help="sfx-test: write WAV files to this folder instead")
    ap.add_argument("--debug", action="store_true", help="log every protocol message")
    ap.add_argument("--scheduling", choices=["silent", "none", "when_idle", "interrupt"],
                    default="none", help="how tool replies are scheduled")
    ap.add_argument("--barge-in", action="store_true", help=argparse.SUPPRESS)   # now the default (earbuds)
    a = ap.parse_args()

    if a.command == "devices":
        from .audio import list_devices
        print(list_devices())
        return
    if a.command == "mic-test":
        from .audio import mic_test
        mic_test(a.mic)
        return
    if a.command == "key-check":
        key_check()
        return
    if a.command == "sfx-test":
        sfx_test(a)
        return
    if a.command == "cameras":
        cameras()
        return
    if a.command == "cam-test":
        cam_test(a)
        return
    if a.command == "local-test":
        local_test(a)
        return
    if a.command in ("memory", "memory-import"):
        from .memory_cli import run
        run(a)
        return
    if a.command == "telegram":
        from .telegram import run_service
        run_service(a)
        return
    if a.command == "voice-test":
        voice_test(a)
        return
    if a.command == "video-test":
        video_test(a)
        return

    from .ui import CyphApp
    if a.sim or not a.port:
        backend = SimBackend()
    else:
        try:
            backend = SerialBackend(a.port)
        except Exception as e:
            raise SystemExit(f"Cannot open the arm on {a.port}: {e}\n"
                             "Close other programs using it, check the cable, or run with --sim.")
    body = Body(backend, wild=max(0.2, min(2.0, a.wild)))
    if not a.no_sfx:
        from .sfx import SfxPlayer
        body.sfx = SfxPlayer(a.speaker, max(0.0, min(1.0, a.sfx_volume)))
    if a.track_gain:
        body.track_gain = a.track_gain
    if a.hfov:
        body.hfov = max(30.0, min(170.0, a.hfov))
    if a.track_flip:
        body.track_sign = -body.track_sign
    camera = None
    if not a.no_camera and a.camera is not None:
        from .vision import Camera
        camera = Camera(a.camera, body, mirror=a.mirror)
    voice = None
    if not a.no_voice and a.brain == "local":
        voice = build_local(body, a, camera)
    elif not a.no_voice:
        from .voice import load_api_key
        key = load_api_key()
        if key:
            from . import voice as voice_mod
            preflight(key, voice_mod.KEY_SOURCE)
            voice = build_voice(body, a, None if a.no_video else camera)
        else:
            body.events.put(("warn", NO_KEY.replace("\n", " ")))
    CyphApp(body, voice, autostart_voice=voice is not None and not a.no_start_voice,
            camera=camera).run()


if __name__ == "__main__":
    main()
