"""The arm agent: the one process on the PC that owns the VectorM's serial port, and the bridge between it and Cyph Core.

    python -m cyph.arm_agent --pair "cyph://pair?host=..&fp=..&code=.."    once: pair this PC (the token goes to cyph/.env)
    python -m cyph.arm_agent                  run it on the real arm (COM15)
    python -m cyph.arm_agent --sim            run it with no arm: the same protocol, a simulated body
    python -m cyph.arm_agent --port COM7

It runs the same Body as the terminal Cyph (python -m cyph), so only one of the two can hold the port at a time.
It streams the arm's state to the server ten times a second and obeys the few commands the server relays. Safety:
  * every angle is clamped again here, by Body, against its own limits, whatever the server or an app sent;
  * a held pose lets go after 45 s without a new command, and the moods play around where the arm was left;
  * Ctrl+C is an E-STOP first (torque off), then it exits; losing the server never stops the arm's own life.
The token is a secret: it lives only in the CYPH_ARM_TOKEN environment variable or the untracked cyph/.env."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import ssl
import sys
import threading
import time
import _thread
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import debuglog

ENV = Path(__file__).resolve().parents[1] / ".env"
STATE_EVERY_S = 0.1
DEFAULT_PORT = "COM15"


def read_env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name].strip()
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def write_env(pairs: dict[str, str]):
    """Set keys in cyph/.env, keeping every other line as it is."""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    for k, v in pairs.items():
        lines = [x for x in lines if not x.strip().startswith(k + "=")] + [f"{k}={v}"]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def pinned_context(host: str, fp: str) -> ssl.SSLContext:
    """A TLS context that trusts exactly the certificate whose SHA-256 is `fp` (the server's is self-signed)."""
    h, _, p = host.rpartition(":")
    pem = ssl.get_server_certificate((h, int(p)), timeout=10)
    der = ssl.PEM_cert_to_DER_cert(pem)
    if hashlib.sha256(der).hexdigest() != fp.lower().replace(":", ""):
        raise SystemExit("The server's certificate is not the one this agent was paired with. Not connecting.")
    ctx = ssl.create_default_context(cadata=pem)
    ctx.check_hostname = False                       # pinned by fingerprint instead (the LAN address is not in its names)
    return ctx


def pair(link: str, name: str):
    import httpx
    u = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if u.scheme != "cyph" or not all(q.get(k) for k in ("host", "fp", "code")):
        raise SystemExit("That is not a Cyph pair link.")
    ctx = pinned_context(q["host"], q["fp"])
    r = httpx.post(f"https://{q['host']}/pair", json={"code": q["code"], "name": name}, verify=ctx, timeout=15)
    if r.status_code != 200:
        raise SystemExit(f"Pairing failed ({r.status_code}): {r.json().get('detail', '')}")
    write_env({"CYPH_CORE_HOST": q["host"], "CYPH_CORE_FP": q["fp"], "CYPH_ARM_TOKEN": r.json()["token"]})
    print(f"Paired as '{name}'. The token is saved in cyph/.env (not shown). Run the agent without --pair now.")


def pid_alive(pid: int) -> bool:
    """Whether a process is still running. (os.kill(pid, 0) is not a probe on Windows: it sends Ctrl+C.)"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259             # STILL_ACTIVE


# what is refused while a task owns the arm: anything that would move it or play a mood
BUSY_REFUSED = {"express", "pet", "wake", "demo", "look", "gesture", "joint", "gripper", "mode", "recenter", "track"}


class Agent:
    def __init__(self, body, host, fp, token, log=print, camera=None, voice=None, noarm=False, brain=None):
        self.body, self.host, self.fp, self.token, self.log = body, host, fp, token, log
        self.brain = brain                               # arm_brain.ArmBrain (Gemini Robotics-ER 2 doing tasks) or None
        self.voice = voice                               # voice_agent.VoiceEngine or None
        self.noarm = noarm                               # no arm plugged in: the body is a stand-in so voice and the face still work
        self.camera = camera                             # vision.Camera or None; frames are sent only while an app watches
        self.cam_on = False
        self.rtt_ms = None                               # round trip to the server, measured with ping/pong
        self.ctrl = None                                 # the controller's last report (real arm only)

    # ---- what the server may ask, each one a call into Body (which clamps and refuses by itself) -------
    def apply(self, c: dict) -> str:
        from .body import JOINT_NAMES
        b, kind = self.body, c.get("cmd")
        if kind in ("voice", "ptt", "voice_cfg", "voice_test", "sleep"):    # these never touch the arm: allowed during an E-STOP
            if self.voice is None:
                raise ValueError("voice is not running on this agent")
            return self.voice.command(c) or kind
        if kind == "task_stop":                          # always allowed, like E-STOP: it only ever makes the arm calmer
            if self.brain is None:
                raise ValueError("tasks are not available on this agent")
            self.brain.stop_task("stopped by you")
            return "task stop"
        if kind == "estop":
            if c["on"] and self.brain is not None:
                self.brain.stop_task("E-STOP")
            b.estop() if c["on"] else b.release()
            return "estop " + ("on" if c["on"] else "released")
        if b.estopped:
            raise ValueError("E-STOP is active: release it first")
        if self.brain is not None and self.brain.state == "running" and kind in BUSY_REFUSED:
            raise ValueError("a task is running: moods and manual moves are off until it ends (Stop ends it)")
        if kind == "express":
            b.express(c["name"])
            return f"mood {c['name']}"
        if kind == "pet":
            b.pet()
            return "pet"
        if kind == "wake":
            b.wake()
            if self.voice is not None:
                self.voice.command(c)
            return "wake"
        if kind == "task":
            if self.brain is None:
                raise ValueError("tasks are not available on this agent")
            if self.noarm:
                raise ValueError("no arm is plugged in")
            return self.brain.start_task(c["goal"])
        if kind == "stop":
            if self.brain is not None:
                self.brain.stop_task("stopped by you")
            b.stop()
            return "stop"
        if kind == "demo":
            b.demo(c.get("names"))
            return "demo tour" if not c.get("names") else "demo " + " ".join(c["names"])
        if kind == "look":
            b.look(c["dir"])
            return f"look {c['dir']}"
        if kind == "gesture":
            b.gesture(c["name"])
            return f"gesture {c['name']}"
        if kind == "speak":
            b.set_speaking(c["on"])
            return "speaking " + ("on" if c["on"] else "off")
        if kind == "listen":
            b.set_listening(c["on"])
            return "listening " + ("on" if c["on"] else "off")
        if kind == "joint":
            b.set_idle(False)                         # a hand on the sliders: the arm stops doing its own thing
            b.move_joints({JOINT_NAMES[c["index"]]: c["angle"]}, relative=False)
            return f"joint {JOINT_NAMES[c['index']]} to {c['angle']:g}"
        if kind == "gripper":
            b.set_gripper(c["action"], force=c.get("force", "gentle"))
            return f"gripper {c['action']}"
        if kind == "mode":
            m = c["mode"]
            if m != "manual":
                b.release_task()                      # back from the sliders: the held joints go back to the moods
            b.set_calm(m == "calm")
            b.set_idle(m != "manual")
            return f"mode {m}"
        if kind == "recenter":
            b.recenter()
            return "recentred"
        if kind == "track":
            b.set_tracking(c["on"])
            return "tracking " + ("on" if c["on"] else "off")
        raise ValueError("unknown command")

    def state(self, first=False) -> dict:
        from .body import MOODS, PASSIVE
        import expressions as ex
        b = self.body
        st = b.status()
        s = {
            "joints": [round(float(v), 1) for v in st["frame"]],
            "mode": "manual" if not st["idle"] else "calm" if st["calm"] else "alive",
            "estop": st["estop"], "mood": st["mood"], "backend": st["backend"], "real": st["real"], "fps": st["fps"],
            "tracking": st["tracking"], "camera": b.camera_ok, "held": st["held"], "speaking": st["speaking"],
            "uptime_s": st["T"], "queued": st["queued"], "resets": st["resets"], "arm_ok": st["arm_ok"],
        }
        if self.noarm:
            s["noarm"] = True
        if self.voice is not None:
            s["voice"] = self.voice.snapshot(first)
        if self.brain is not None:
            s["brain"] = self.brain.snapshot()
        if self.rtt_ms is not None:
            s["rtt_ms"] = round(self.rtt_ms, 1)
        if self.ctrl:
            s["ctrl"] = self.ctrl
        cam = self.camera
        if cam is not None:
            c = {"state": cam.state, "fps": round(cam.fps, 1), "w": cam.size[0], "h": cam.size[1], "stereo": cam.stereo,
                 "det": cam.detector_kind}
            if cam.brightness is not None:
                c["bright"] = round(cam.brightness, 1)
                c["motion"] = round(cam.motion, 2)
            p = cam.person
            if p:
                c["person"] = {"x": float(p[0]), "y": float(p[1])}
                if p[2]:
                    c["dist_cm"] = round(float(p[2]) * 100)
            s["cam"] = c
        if first:
            s["expressions"] = [{"name": n, "secs": ex.EXPRESSIONS[n][1], "passive": n in PASSIVE} for n in MOODS]
        return s

    # ---- the link --------------------------------------------------------------------------------------
    async def run(self):
        import websockets
        delay = 1.0
        while True:
            try:
                ctx = await asyncio.get_running_loop().run_in_executor(None, pinned_context, self.host, self.fp)
                debuglog.info("link: connecting to %s", self.host)
                async with websockets.connect(f"wss://{self.host}/arm/agent", ssl=ctx, ping_interval=20, max_size=2**20) as ws:
                    await ws.send(json.dumps({"type": "auth", "token": self.token}))
                    hello = json.loads(await ws.recv())
                    if hello.get("type") != "ready":
                        raise ConnectionError("not accepted")
                    self.log("link up: server has the arm")
                    debuglog.info("link: UP")
                    delay = 1.0
                    self.cam_on, self.rtt_ms = False, None
                    await asyncio.gather(self.send_loop(ws), self.recv_loop(ws), self.video_loop(ws), self.ping_loop(ws), self.diag_loop())
            except SystemExit:
                raise
            except Exception as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                if code == 4401:
                    raise SystemExit("The server no longer accepts this agent's token. Pair it again with --pair.")
                self.log(f"link down ({type(e).__name__}); retrying in {delay:.0f}s")
                debuglog.exc("link: DOWN (%s: %s, close code %s); retry in %.0fs", type(e).__name__, e, code, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)

    async def send_loop(self, ws):
        first, n = True, 0
        loop = asyncio.get_running_loop()
        fixed_at = 0.0
        while True:
            if n % 2 == 0:                               # the controller is asked about 5 times a second, off the loop
                self.ctrl = await loop.run_in_executor(None, self.body.backend.read)
                if self.ctrl and not self.ctrl["torque"] and not self.body.estopped and time.monotonic() - fixed_at > 5:
                    fixed_at = time.monotonic()          # outputs off with no E-STOP: a reset or a leftover. Turn them on.
                    try:
                        await loop.run_in_executor(None, self.body.backend.torque, True)
                    except Exception:
                        continue                         # the arm just went away: the body is reconnecting it
                    await ws.send(json.dumps({"type": "log", "level": "warn", "text": "controller outputs were off: torque turned back on"}))
            n += 1
            await ws.send(json.dumps({"type": "state", "state": self.state(first)}))
            first = False
            while True:                                  # what the body logged since last time (moods, e-stops, warnings)
                try:
                    level, text = self.body.events.get_nowait()
                except Exception:
                    break
                debuglog.log.log({"error": 40, "warn": 30}.get(level, 20), "event: %s", text)
                await ws.send(json.dumps({"type": "log", "level": level, "text": text}))
            await asyncio.sleep(STATE_EVERY_S)

    async def diag_loop(self):
        """One line of the whole picture every few seconds (every second during a task), so a failure can be put in context."""
        while True:
            b, c, br = self.body, self.camera, self.brain
            running = br is not None and br.state == "running"
            try:
                st = b.status()
                debuglog.info(
                    "status arm_ok=%s estop=%s working=%s mood=%s joints=%s queued=%s resets=%s | cam=%s %sfps | brain=%s calls=%s last=%s | "
                    "voice=%s | rtt=%sms",
                    st["arm_ok"], st["estop"], b.working, st["mood"], [round(float(v)) for v in st["frame"]], st["queued"], st["resets"],
                    getattr(c, "state", "-"), round(getattr(c, "fps", 0) or 0), getattr(br, "state", "-"), getattr(br, "calls", "-"),
                    getattr(br, "last", "-"), (self.voice.snapshot().get("state") if self.voice else "-"),
                    None if self.rtt_ms is None else round(self.rtt_ms))
            except Exception:
                debuglog.exc("diag failed")
            await asyncio.sleep(1.0 if running else 5.0)

    async def video_loop(self, ws):
        """About 10 JPEG frames a second from the camera, but only while the server says an app is looking."""
        loop = asyncio.get_running_loop()
        while True:
            cam = self.camera
            if self.cam_on and cam is not None and cam.state == "live":
                data = await loop.run_in_executor(None, cam.jpeg, 640, 62)
                if data:
                    await ws.send(data)
                await asyncio.sleep(0.09)
            else:
                await asyncio.sleep(0.2)

    async def ping_loop(self, ws):
        while True:
            await ws.send(json.dumps({"type": "ping", "t": time.monotonic()}))
            await asyncio.sleep(2.0)

    async def recv_loop(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "cam":
                self.cam_on = bool(msg.get("on"))
                continue
            if msg.get("type") == "pong":
                if isinstance(msg.get("t"), (int, float)):
                    self.rtt_ms = (time.monotonic() - msg["t"]) * 1000
                continue
            if msg.get("type") != "cmd":
                continue
            kind = msg.get("cmd")
            if kind not in ("joint", "ptt"):                 # the sliders send ten a second
                debuglog.info("cmd: %s %s", kind, msg.get("goal", "") if kind == "task" else "")
            try:
                done = self.apply(msg)
                await ws.send(json.dumps({"type": "log", "level": "info", "text": "app: " + done}))
            except (ValueError, KeyError, TypeError) as e:
                debuglog.warn("cmd %s refused: %s", kind, e)
                await ws.send(json.dumps({"type": "log", "level": "warn", "text": f"refused: {e}"}))
            except Exception as e:                           # a bug in a command must not drop the link to the server
                debuglog.exc("cmd %s CRASHED", kind)
                await ws.send(json.dumps({"type": "log", "level": "error", "text": f"command failed: {type(e).__name__}: {e}"}))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m cyph.arm_agent")
    ap.add_argument("--pair", metavar="LINK", help="pair this PC with a pair link, then exit")
    ap.add_argument("--name", default="Arm agent (PC)")
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--sim", action="store_true", help="no arm: a simulated body")
    ap.add_argument("--camera", default="auto", help="webcam index, or auto: the arm's stereo camera (python -m cyph cameras lists them)")
    ap.add_argument("--no-camera", action="store_true", help="run without the camera (no video, no face tracking)")
    ap.add_argument("--mirror", action="store_true", help="flip the camera image left/right")
    ap.add_argument("--parent-pid", type=int, default=0,
                    help="started by the Cyph app: when that process ends, rest the arm and exit")
    ap.add_argument("--no-voice", action="store_true", help="no voice: no microphone, no speaking")
    ap.add_argument("--no-brain", action="store_true", help="no tasks: Gemini Robotics-ER 2 never drives the arm")
    ap.add_argument("--no-sfx", action="store_true", help="no sound effects")
    ap.add_argument("--sfx-volume", type=float, default=0.35)
    ap.add_argument("--speaker", default="CMF Buds 2", help="speaker for the sound effects: a name fragment or device number")
    ap.add_argument("--wild", type=float, default=1.5, help="how big the idle moods are (0.2 to 2)")
    a = ap.parse_args(argv)
    if a.pair:
        return pair(a.pair, a.name)
    debuglog.setup()
    debuglog.info("arm agent starting: %s", {k: v for k, v in vars(a).items() if k != "pair"})
    host, fp, token = read_env("CYPH_CORE_HOST"), read_env("CYPH_CORE_FP"), read_env("CYPH_ARM_TOKEN")
    if not (host and fp and token):
        raise SystemExit('Not paired yet. Get a link (app: Settings > Pair a new device) and run:  python -m cyph.arm_agent --pair "<link>"')
    from .body import Body, SerialBackend, SimBackend
    noarm = False
    if a.sim:
        backend = SimBackend()
    else:
        try:
            backend = SerialBackend(a.port)
        except Exception as e:                           # no arm plugged in: voice, the face and the camera still work
            print(f"no arm on {a.port} ({type(e).__name__}: {e}); running without it", flush=True)
            backend, noarm = SimBackend(), True
    body = Body(backend, wild=max(0.2, min(2.0, a.wild)))
    first = body.backend.read()
    if first is not None and not first["torque"]:        # the last agent's shutdown cut torque; a new one must turn it back on
        body.backend.stop()
        body.backend.torque(True)
        print("controller outputs were off (left by the last run): torque is on again", flush=True)
    if not a.no_sfx:                                     # the same mood and event sounds as the terminal Cyph
        try:
            from .sfx import SfxPlayer
            body.sfx = SfxPlayer(a.speaker, max(0.0, min(1.0, a.sfx_volume)))
            body.sfx.start()
            print(f"sound effects: {body.sfx.label}", flush=True)
            body.sfx_play("boot")
        except Exception as e:
            body.sfx = None
            print(f"sound effects: off ({type(e).__name__}: {e})", flush=True)
    body.start()
    camera = None
    if not a.no_camera:
        try:
            from .vision import Camera, resolve_camera
            camera = Camera(resolve_camera(a.camera, 1), body, mirror=a.mirror)
            camera.start()
        except Exception as e:                           # no opencv, no camera: the arm still works
            camera = None
            print(f"camera: not used ({type(e).__name__}: {e})", flush=True)
    print(f"arm agent: {body.backend.name} -> {host}   camera: {'on' if camera else 'off'}   (Ctrl+C = E-STOP and exit)", flush=True)
    voice = None
    if not a.no_voice:
        try:
            from .voice_agent import ChatLink, VoiceEngine
            def vlog(level, text):                       # shows in the app's Events list, so never put speech in it
                body.events.put((level if level in ("info", "warn", "error") else "info", text))
            voice = VoiceEngine(body, ChatLink(host, fp, token, vlog), camera=camera, log=vlog)
            voice.start()
        except Exception as e:
            voice = None
            print(f"voice: not started ({type(e).__name__}: {e})", flush=True)
    brain = None
    if not a.no_brain:
        try:
            from .arm_brain import ArmBrain
            def blog(level, text):
                body.events.put((level if level in ("info", "warn", "error") else "info", "brain: " + text))
            brain = ArmBrain(body, camera, say=voice.say if voice is not None else None, log=blog)
        except Exception as e:
            print(f"task brain: not started ({type(e).__name__}: {e})", flush=True)
    def alog(t):
        print(time.strftime("%H:%M:%S"), t, flush=True)
        debuglog.info("agent: %s", t)
    debuglog.info("ready: backend=%s noarm=%s camera=%s voice=%s brain=%s host=%s", body.backend.name, noarm, camera is not None,
                  voice is not None, brain is not None, host)
    agent = Agent(body, host, fp, token, log=alog, camera=camera, voice=voice, noarm=noarm, brain=brain)
    parent_gone = threading.Event()
    if a.parent_pid:
        def watch_parent():
            while pid_alive(a.parent_pid):
                time.sleep(1.0)
            parent_gone.set()
            _thread.interrupt_main()                     # the same path as Ctrl+C
        threading.Thread(target=watch_parent, name="parent-watch", daemon=True).start()
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        debuglog.warn("exit: Ctrl+C or the app closed (parent gone: %s)", parent_gone.is_set())
    except BaseException:
        debuglog.exc("exit: the agent's main loop died")
        raise
    finally:
        debuglog.info("shutting down: resting the arm, stopping the camera")
        if parent_gone.is_set():                         # the app was closed: settle to rest before going limp
            print("the app closed: resting the arm", flush=True)
            body.stop()
            time.sleep(2.5)
        body.estop()                                     # local E-STOP first: torque off, whatever else fails
        time.sleep(0.3)
        if camera is not None:
            camera.stop()
        if body.sfx is not None:
            body.sfx.stop()
        body.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
